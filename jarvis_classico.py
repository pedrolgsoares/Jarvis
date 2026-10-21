"""Jarvis clássico: chatbot sem API, sem chave, que aprende com você.

Só usa a biblioteca padrão do Python (3.9+). Basta rodar:
    python jarvis_classico.py

Como funciona:
  1. Você fala algo.
  2. Ele procura na base uma pergunta igual ou parecida.
  3. Achou? Responde com uma das respostas que conhece.
  4. Não achou? Pede para você ensinar a resposta e grava na base.

Comandos:
    /errado  -> a última resposta foi ruim: apaga e ensina uma melhor
    /stats   -> mostra quanto ele já sabe
    /sair    -> encerra
"""

import difflib
import json
import random
import re
import unicodedata
from pathlib import Path

BASE = Path(__file__).with_name("conhecimento.json")
LIMIAR = 0.62  # 0 a 1: quanto menor, mais "aceita" frases parecidas

SEMENTE = {
    "oi": ["Olá!", "Oi! Tudo bem?", "E aí!"],
    "ola": ["Olá!", "Oi, tudo bem?"],
    "bom dia": ["Bom dia! Pronto para mais um dia de código?"],
    "boa noite": ["Boa noite! Não vá virar a madrugada programando."],
    "tudo bem": ["Tudo ótimo por aqui, e com você?"],
    "quem e voce": ["Sou o Jarvis, um chatbot que aprende com o que você me ensina."],
    "qual e o seu nome": ["Jarvis. Pode me chamar assim."],
    "o que voce faz": ["Converso e aprendo frases novas com você."],
    "obrigado": ["De nada!", "Disponha!"],
    "tchau": ["Até mais!", "Até logo!"],
}


def normalizar(texto: str) -> str:
    """minúsculas, sem acentos, sem pontuação, sem espaços sobrando."""
    texto = unicodedata.normalize("NFD", texto.lower())
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    texto = re.sub(r"[^a-z0-9\s]", " ", texto)
    return " ".join(texto.split())


def salvar(base: dict) -> None:
    BASE.write_text(json.dumps(base, ensure_ascii=False, indent=2), encoding="utf-8")


def carregar() -> dict:
    if BASE.exists():
        return json.loads(BASE.read_text(encoding="utf-8"))
    base = {normalizar(pergunta): respostas for pergunta, respostas in SEMENTE.items()}
    salvar(base)
    return base


def buscar(base: dict, pergunta: str) -> str | None:
    """Devolve a chave mais parecida com a pergunta, ou None se não houver."""
    if pergunta in base:
        return pergunta
    parecidas = difflib.get_close_matches(pergunta, base.keys(), n=1, cutoff=LIMIAR)
    return parecidas[0] if parecidas else None


def ensinar(base: dict, chave: str) -> bool:
    """Pergunta ao usuário qual resposta dar. Devolve True se aprendeu algo."""
    resposta = input("Ensinar (Enter para pular): ").strip()
    if not resposta:
        return False
    base.setdefault(chave, []).append(resposta)
    salvar(base)
    print("Jarvis: Aprendi! Da próxima vez eu sei responder.")
    return True


def main() -> None:
    base = carregar()
    ultima_chave: str | None = None
    ultima_resposta: str | None = None

    print(f"Jarvis clássico online. Já sei {len(base)} frases. /sair para encerrar.")

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
            continue

        chave = normalizar(entrada)
        if not chave:
            continue

        achada = buscar(base, chave)
        if achada and base[achada]:
            ultima_resposta = random.choice(base[achada])
            ultima_chave = achada
            print(f"Jarvis: {ultima_resposta}")
        else:
            print("Jarvis: Não sei responder isso ainda. O que eu deveria dizer?")
            if ensinar(base, chave):
                ultima_chave, ultima_resposta = chave, base[chave][-1]


if __name__ == "__main__":
    main()