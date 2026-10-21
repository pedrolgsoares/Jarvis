"""Jarvis estilo ELIZA (1966): responde por regras e padrões (regex), sem aprender e sem API.

Só usa a biblioteca padrão do Python. Basta rodar:
    python jarvis_eliza.py

Como funciona:
  1. Limpa o texto (minúsculas, sem pontuação).
  2. Percorre as REGRAS em ordem. A primeira cujo padrão casar vence.
  3. Pega os trechos capturados pelo padrão, troca os pronomes
     ("meu" -> "seu", "eu" -> "você"...) e encaixa nos modelos de resposta.
  4. Se nada casar, usa uma resposta genérica ou volta a algo que você
     falou antes (a "memória" da ELIZA original).

Digite "tchau" ou "/sair" para encerrar.
"""

import random
import re

# ---------------------------------------------------------------------------
# 1) Reflexão de pronomes: vira o ponto de vista de quem fala -> quem responde
# ---------------------------------------------------------------------------
REFLEXOES = {
    "eu": "você", "você": "eu", "voce": "eu",
    "meu": "seu", "seu": "meu", "minha": "sua", "sua": "minha",
    "meus": "seus", "seus": "meus", "minhas": "suas", "suas": "minhas",
    "me": "te", "te": "me", "mim": "você", "ti": "mim",
    "comigo": "com você", "contigo": "comigo",
    "sou": "é", "é": "sou", "estou": "está", "está": "estou",
    "tenho": "tem", "tem": "tenho", "vou": "vai", "vai": "vou", "fui": "foi",
}


def refletir(fragmento: str) -> str:
    palavras = fragmento.split()
    saida: list[str] = []
    i = 0
    while i < len(palavras):
        # "voce e" (sem acento) é ambíguo palavra a palavra, então tratamos o par junto
        if palavras[i] in ("você", "voce") and i + 1 < len(palavras) and palavras[i + 1] in ("é", "e"):
            saida.append("eu sou")
            i += 2
            continue
        saida.append(REFLEXOES.get(palavras[i], palavras[i]))
        i += 1
    return " ".join(saida)


# ---------------------------------------------------------------------------
# 2) Regras: (padrão, modelos de resposta, guarda_na_memória)
#    {0}, {1}... são os trechos capturados (já com pronomes trocados).
#    A ORDEM IMPORTA: regras mais específicas primeiro.
# ---------------------------------------------------------------------------
REGRAS: list[tuple[re.Pattern, list[str], bool]] = []


def regra(padrao: str, respostas: list[str], memoria: bool = False) -> None:
    REGRAS.append((re.compile(padrao, re.IGNORECASE), respostas, memoria))


regra(r"^(?:oi|ol[áa]|bom dia|boa tarde|boa noite)\b", [
    "Olá. Como você está se sentindo hoje?",
    "Oi! Sobre o que você gostaria de conversar?",
])
regra(r"\bdesculp\w*", [
    "Não precisa pedir desculpas.",
    "Desculpas não são necessárias. O que você sente quando diz isso?",
])
regra(r"\blembro (?:de |que )?(.+)", [
    "Você costuma pensar em {0}?",
    "Por que você se lembrou disso agora?",
    "O que mais você lembra?",
])
regra(r"\bsonh(?:o|ei|ando)\b", [
    "Conte mais sobre esse sonho.",
    "O que você acha que esse sonho quer dizer?",
])
regra(r"\beu (?:estou|to|tô) (.+)", [
    "Há quanto tempo você está {0}?",
    "Por que você está {0}?",
    "Você gosta de estar {0}?",
])
regra(r"\beu sou (.+)", [
    "Por que você diz que é {0}?",
    "Há quanto tempo você é {0}?",
])
regra(r"\beu me sinto (.+)", [
    "Por que você se sente {0}?",
    "Com que frequência você se sente {0}?",
])
regra(r"\beu (?:quero|queria) (.+)", [
    "O que significaria para você conseguir {0}?",
    "Por que você quer {0}?",
])
regra(r"\beu preciso (.+)", [
    "Por que você precisa {0}?",
    "O que mudaria se você conseguisse isso?",
])
regra(r"\beu n[ãa]o (?:consigo|posso) (.+)", [
    "O que impede você de {0}?",
    "Você já tentou {0}?",
])
regra(r"\beu gosto (.+)", [
    "O que faz você gostar {0}?",
    "Há quanto tempo você gosta {0}?",
])
regra(r"\beu (?:tenho|tive) (.+)", [
    "Como é para você ter {0}?",
    "Isso afeta o seu dia a dia?",
])
regra(r"\beu (?:acho|penso|creio) que (.+)", [
    "Por que você acha que {0}?",
    "Você tem certeza de que {0}?",
])
regra(r"\bvoc[eê] [eé] (.+)", [
    "O que faz você pensar que eu sou {0}?",
    "Isso importa para você, eu ser {0}?",
])
regra(r"\bvoc[eê] (?:pode|consegue) (.+)", [
    "Você acredita que eu consigo {0}?",
    "Talvez você mesmo possa {0}.",
])
regra(r"\bvoc[eê]\b", [
    "Estamos falando de você, não de mim.",
    "Por que você quer saber sobre mim?",
    "Vamos voltar a você. O que está acontecendo?",
])
regra(r"\b(?:m[ãa]e|pai|irm[ãa]o|irm[ãa]|filh[oa]|esposa|marido|namorad[oa]|fam[ií]lia)\b", [
    "Fale mais sobre a sua família.",
    "Como é a sua relação com essa pessoa?",
])
# guarda_na_memória=True: o que foi dito aqui pode voltar à conversa depois
regra(r"\b(meu|minha|meus|minhas) (\w+)", [
    "Fale mais sobre {0} {1}.",
    "Como você se sente em relação a {0} {1}?",
], memoria=True)
regra(r"\b(?:sempre|nunca)\b", [
    "Consegue pensar em um exemplo específico?",
    "Realmente? Sempre assim?",
])
regra(r"\b(?:computador|rob[ôo]|ia|intelig[êe]ncia artificial)\b", [
    "Você se preocupa com máquinas?",
    "O que você acha das máquinas que conversam?",
])
regra(r"^(?:sim|claro|com certeza)$", [
    "Você parece bem seguro disso.",
    "Entendi. Pode explicar melhor?",
])
regra(r"^n[ãa]o$", [
    "Por que não?",
    "Você está sendo um pouco curto. Quer explicar?",
])

FALLBACKS = [
    "Continue, estou ouvindo.",
    "Pode explicar melhor?",
    "Interessante. E o que você sente sobre isso?",
    "Entendo. Prossiga.",
    "Isso é importante para você?",
]


# ---------------------------------------------------------------------------
# 3) O motor
# ---------------------------------------------------------------------------
class Eliza:
    def __init__(self) -> None:
        self.memoria: list[str] = []
        self.ultimo: dict[int, int] = {}  # evita repetir a mesma resposta seguida

    @staticmethod
    def limpar(texto: str) -> str:
        texto = re.sub(r"[.!?,;:]+", " ", texto.lower())
        return " ".join(texto.split())

    def escolher(self, chave: int, opcoes: list[str]) -> str:
        if len(opcoes) == 1:
            return opcoes[0]
        candidatos = [i for i in range(len(opcoes)) if i != self.ultimo.get(chave)]
        indice = random.choice(candidatos)
        self.ultimo[chave] = indice
        return opcoes[indice]

    def responder(self, texto: str) -> str:
        limpo = self.limpar(texto)

        for numero, (padrao, respostas, guarda) in enumerate(REGRAS):
            achou = padrao.search(limpo)
            if not achou:
                continue
            trechos = [refletir(g.strip()) for g in achou.groups() if g]
            if guarda and trechos:
                self.memoria.append(" ".join(trechos))
            return self.escolher(numero, respostas).format(*trechos)

        # nada casou: às vezes retoma algo que o usuário disse antes
        if self.memoria and random.random() < 0.5:
            return f"Antes você falou sobre {self.memoria.pop(0)}. Quer voltar a isso?"
        return self.escolher(-1, FALLBACKS)


def main() -> None:
    eliza = Eliza()
    print("Jarvis (modo ELIZA) online. Digite 'tchau' para sair.")
    print("Jarvis: Olá. Como você está se sentindo hoje?")

    while True:
        try:
            entrada = input("\nVocê: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nAté logo!")
            break

        if not entrada:
            continue
        if entrada.lower() in ("tchau", "sair", "/sair", "exit"):
            print("Jarvis: Até logo. Foi bom conversar com você.")
            break

        print(f"Jarvis: {eliza.responder(entrada)}")


if __name__ == "__main__":
    main()