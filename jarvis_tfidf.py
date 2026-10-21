"""Jarvis clássico, versão TF-IDF: a busca compara PALAVRAS, não letras.

Requisitos:
    pip install scikit-learn

Coloque este arquivo na MESMA pasta do jarvis_classico.py: ele reaproveita a
normalização de texto e o conhecimento.json que você já foi ensinando.

Uso:
    python jarvis_tfidf.py

Comandos:
    /debug   -> liga/desliga a exibição da similaridade (ótimo para estudar)
    /errado  -> a última resposta foi ruim: apaga e ensina uma melhor
    /stats   -> mostra quanto ele já sabe
    /sair    -> encerra
"""

import random

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from jarvis_classico import carregar, normalizar, salvar

LIMIAR = 0.35  # similaridade mínima (0 a 1) para considerar que é a mesma pergunta

# Palavras que aparecem em tudo e não ajudam a distinguir uma frase da outra.
# (já sem acento, porque o texto passa por normalizar() antes)
STOPWORDS = [
    "um", "uma", "de", "do", "da", "dos", "das", "em", "no", "na", "nos", "nas",
    "ou", "me", "te", "se", "seu", "sua", "teu", "tua", "meu", "minha",
]


class Buscador:
    """Transforma as perguntas conhecidas em vetores TF-IDF e compara por cosseno."""

    def __init__(self, base: dict) -> None:
        self.reconstruir(base)

    def reconstruir(self, base: dict) -> None:
        """Refaz os vetores. Chame sempre que a base mudar (é rápido para bases pequenas)."""
        self.chaves = [pergunta for pergunta, respostas in base.items() if respostas]
        self.vetorizador = None
        self.matriz = None
        if not self.chaves:
            return
        vetorizador = TfidfVectorizer(stop_words=STOPWORDS)
        try:
            self.matriz = vetorizador.fit_transform(self.chaves)
            self.vetorizador = vetorizador
        except ValueError:
            # vocabulário vazio (só stopwords): fica sem busca até haver frases melhores
            self.vetorizador = None

    def buscar(self, pergunta: str) -> tuple[str | None, float]:
        """Devolve (pergunta_conhecida_mais_parecida, similaridade)."""
        if pergunta in self.chaves:
            return pergunta, 1.0
        if self.vetorizador is None:
            return None, 0.0

        vetor = self.vetorizador.transform([pergunta])
        if vetor.nnz == 0:  # nenhuma palavra da pergunta existe na base
            return None, 0.0

        similaridades = cosine_similarity(vetor, self.matriz)[0]
        melhor = int(similaridades.argmax())

        # O vetorizador ignora palavras que a base nunca viu. Sem este ajuste,
        # "o que é uma lista encadeada?" casaria com "o que você faz?" só por causa
        # do "que". Então reduzimos o score quando sobram muitas palavras desconhecidas.
        tokens = self.vetorizador.build_analyzer()(pergunta)
        conhecidas = sum(1 for t in tokens if t in self.vetorizador.vocabulary_)
        cobertura = conhecidas / len(tokens) if tokens else 0.0
        score = float(similaridades[melhor]) * (0.5 + 0.5 * cobertura)
        return self.chaves[melhor], score


def ensinar(base: dict, chave: str) -> bool:
    resposta = input("Ensinar (Enter para pular): ").strip()
    if not resposta:
        return False
    base.setdefault(chave, []).append(resposta)
    salvar(base)
    print("Jarvis: Aprendi! Da próxima vez eu sei responder.")
    return True


def main() -> None:
    base = carregar()
    buscador = Buscador(base)
    debug = False
    ultima_chave: str | None = None
    ultima_resposta: str | None = None

    print(f"Jarvis TF-IDF online. Já sei {len(base)} frases. /sair para encerrar, /debug para ver os scores.")

    while True:
        try:
            entrada = input("\nVocê: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nAté logo!")
            break

        if not entrada:
            continue

        comando = entrada.lower()
        if comando in ("/sair", "sair"):
            print("Até logo!")
            break

        if comando == "/debug":
            debug = not debug
            print(f"Jarvis: debug {'ligado' if debug else 'desligado'}.")
            continue

        if comando == "/stats":
            total = sum(len(r) for r in base.values())
            print(f"Jarvis: Conheço {len(base)} frases e {total} respostas.")
            continue

        if comando == "/errado":
            if not ultima_chave:
                print("Jarvis: Ainda não respondi nada para corrigir.")
                continue
            respostas = base.get(ultima_chave, [])
            if ultima_resposta in respostas:
                respostas.remove(ultima_resposta)
            print(f'Jarvis: Ok, esqueci aquela resposta. O que eu deveria dizer para "{ultima_chave}"?')
            ensinar(base, ultima_chave)
            if not base.get(ultima_chave):
                base.pop(ultima_chave, None)
                salvar(base)
            buscador.reconstruir(base)
            continue

        chave = normalizar(entrada)
        if not chave:
            continue

        achada, score = buscador.buscar(chave)
        if debug:
            print(f"[debug] melhor: {achada!r}  similaridade: {score:.2f}  limiar: {LIMIAR}")

        if achada and score >= LIMIAR and base.get(achada):
            ultima_resposta = random.choice(base[achada])
            ultima_chave = achada
            print(f"Jarvis: {ultima_resposta}")
        else:
            print("Jarvis: Não sei responder isso ainda. O que eu deveria dizer?")
            if ensinar(base, chave):
                buscador.reconstruir(base)
                ultima_chave, ultima_resposta = chave, base[chave][-1]


if __name__ == "__main__":
    main()
