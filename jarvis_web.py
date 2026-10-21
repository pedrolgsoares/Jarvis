"""Ferramentas de web do Jarvis: informação ao vivo que o modelo não tem como saber.

  CLIMA -> Open-Meteo (grátis, sem chave). Só o nome da cidade sai do seu PC.
  BUSCA -> DuckDuckGo via pacote ddgs (grátis, sem chave). A sua pergunta vai para eles.

    pip install ddgs

Variáveis de ambiente (opcionais, podem ficar no .env):
    JARVIS_CIDADE   cidade usada quando a pergunta de clima não cita nenhuma (ex.: "Guarapuava")
    JARVIS_PAIS     código do país para achar a cidade certa (padrão BR)
    JARVIS_WEB      0 desliga toda a web (padrão: ligada)
"""

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

GEOCODIFICACAO_URL = "https://geocoding-api.open-meteo.com/v1/search"
PREVISAO_URL = "https://api.open-meteo.com/v1/forecast"
PAIS = os.getenv("JARVIS_PAIS", "BR")
WEB_LIGADA = os.getenv("JARVIS_WEB", "1") != "0"


class WebIndisponivel(Exception):
    """A web não pôde ajudar agora (sem internet, serviço fora do ar, sem resultado...)."""


# --------------------------------------------------------------------------
# Que tipo de pergunta é?
# --------------------------------------------------------------------------
_CLIMA = re.compile(
    r"\b(clima|previs[aã]o do tempo|temperatura|vai chover|chover|chuva|"
    r"tempo (hoje|amanh[ãa]|agora|em|na|no|l[áa] fora|est[áa])|como est[áa] o tempo|que tempo)\b",
    re.IGNORECASE,
)
_ATUAL = re.compile(
    r"\b(not[ií]cias?|cota[çc][ãa]o|d[óo]lar|euro|bitcoin|[úu]ltimas?|placar|resultado d[oa]|"
    r"pesquis[ae]|busque|procure na (web|internet)|na internet|na web)\b",
    re.IGNORECASE,
)
_NAO_CIDADE = {"hoje", "amanhã", "amanha", "agora", "aqui", "lá", "la", "fora", "semana", "manhã",
               "manha", "tarde", "noite", "como", "vai", "está", "esta", "tempo", "clima",
               "previsão", "previsao", "temperatura", "chuva", "que", "quanto", "qual"}


def pergunta_de_clima(texto: str) -> bool:
    return WEB_LIGADA and bool(_CLIMA.search(texto))


def pergunta_atual(texto: str) -> bool:
    """Notícias, cotações, placares... coisas que mudam e que o modelo não sabe."""
    return WEB_LIGADA and bool(_ATUAL.search(texto))


def _cidade_da_pergunta(texto: str) -> str:
    """'clima em São Paulo hoje' -> 'São Paulo'. Sem cidade citada, usa JARVIS_CIDADE."""
    # lookahead: os candidatos podem se sobrepor ("do tempo para amanhã em curitiba" tem dois)
    for achado in re.finditer(r"(?=\b(?:em|para|pra|de|do|da|na|no)\s+((?:[^\W\d_]+[ -]?){1,4}))", texto, re.IGNORECASE):
        palavras: list[str] = []
        for palavra in achado.group(1).split():
            if palavra.lower().strip("?!.,") in _NAO_CIDADE:
                break
            palavras.append(palavra.strip("?!.,"))
        if palavras:
            return " ".join(palavras)
    return os.getenv("JARVIS_CIDADE", "").strip()


# --------------------------------------------------------------------------
# Clima (Open-Meteo)
# --------------------------------------------------------------------------
def _obter_json(url: str, parametros: dict) -> dict:
    try:
        with urllib.request.urlopen(f"{url}?{urllib.parse.urlencode(parametros)}", timeout=10) as resposta:
            return json.loads(resposta.read().decode("utf-8"))
    except urllib.error.HTTPError as erro:
        raise WebIndisponivel(f"HTTP {erro.code}") from erro
    except (OSError, ValueError) as erro:  # sem internet, timeout, JSON inválido
        raise WebIndisponivel(str(erro)) from erro


def _descrever_codigo(codigo: int) -> str:
    if codigo == 0:
        return "céu limpo"
    if codigo in (1, 2):
        return "parcialmente nublado"
    if codigo == 3:
        return "nublado"
    if codigo in (45, 48):
        return "com neblina"
    if 51 <= codigo <= 57:
        return "com garoa"
    if 61 <= codigo <= 67 or codigo in (80, 81, 82):
        return "com chuva"
    if 71 <= codigo <= 77 or codigo in (85, 86):
        return "com neve"
    if codigo >= 95:
        return "com tempestade"
    return "tempo instável"


def clima(pergunta: str) -> str:
    """Resposta falada com o tempo agora (ou amanhã, se a pergunta pedir)."""
    cidade = _cidade_da_pergunta(pergunta)
    if not cidade:
        raise WebIndisponivel("diga a cidade, ou defina JARVIS_CIDADE no .env")

    achados = None
    palavras = cidade.split()
    while palavras and not achados:  # "Guarabira Paraíba" não existe: tira palavras do fim até achar
        nome = " ".join(palavras)
        achados = _obter_json(GEOCODIFICACAO_URL, {"name": nome, "count": 1, "language": "pt",
                                                   "country_code": PAIS}).get("results")
        palavras.pop()
    if not achados:  # cidade fora do país padrão: tenta no mundo todo
        achados = _obter_json(GEOCODIFICACAO_URL, {"name": cidade.split()[0], "count": 1,
                                                   "language": "pt"}).get("results")
    if not achados:
        raise WebIndisponivel(f"não achei a cidade '{cidade}'")
    local = achados[0]

    dados = _obter_json(PREVISAO_URL, {
        "latitude": local["latitude"], "longitude": local["longitude"], "timezone": "auto",
        "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
        "forecast_days": 2,
    })
    try:
        atual, dia = dados["current"], dados["daily"]
        quando = 1 if re.search(r"amanh[ãa]", pergunta, re.IGNORECASE) else 0
        maxima, minima = round(dia["temperature_2m_max"][quando]), round(dia["temperature_2m_min"][quando])
        chuva = dia["precipitation_probability_max"][quando]
        nome = local["name"]
        if quando:
            texto = (f"Amanhã em {nome}: {_descrever_codigo(dia['weather_code'][1])}, "
                     f"máxima de {maxima} graus e mínima de {minima}")
        else:
            texto = (f"Agora em {nome} faz {round(atual['temperature_2m'])} graus, "
                     f"{_descrever_codigo(atual['weather_code'])}. "
                     f"Sensação de {round(atual['apparent_temperature'])}. "
                     f"Hoje a máxima é {maxima} e a mínima {minima}")
        if chuva is not None:
            texto += f", com {chuva} por cento de chance de chuva"
        return texto + "."
    except (KeyError, IndexError, TypeError) as erro:
        raise WebIndisponivel("resposta inesperada da previsão") from erro


# --------------------------------------------------------------------------
# Busca geral (DuckDuckGo)
# --------------------------------------------------------------------------
def buscar(pergunta: str, quantos: int = 4) -> list[dict]:
    """Devolve [{'titulo', 'trecho', 'link'}] ou levanta WebIndisponivel."""
    if not WEB_LIGADA:
        raise WebIndisponivel("web desligada (JARVIS_WEB=0)")
    try:
        from ddgs import DDGS
    except ImportError as erro:
        raise WebIndisponivel("falta instalar: pip install ddgs") from erro
    try:
        achados = DDGS().text(pergunta, region="br-pt", max_results=quantos)
    except Exception as erro:  # a biblioteca levanta tipos próprios (limite, rede, bloqueio...)
        raise WebIndisponivel(f"busca: {erro}"[:120]) from erro
    resultados = [{"titulo": a.get("title", ""), "trecho": a.get("body", ""), "link": a.get("href", "")}
                  for a in achados or [] if a.get("body")]
    if not resultados:
        raise WebIndisponivel("a busca não achou nada")
    return resultados


def formatar_contexto(resultados: list[dict]) -> str:
    """Resultados da busca como contexto para o modelo resumir."""
    blocos = "\n\n".join(f"[resultado {i} - {r['titulo']}]\n{r['trecho']}" for i, r in enumerate(resultados, 1))
    return ("Use os resultados de busca na web abaixo, que são recentes, para responder. "
            "Se não bastarem, diga que não achou.\n\n" + blocos)


def resumo_cru(resultados: list[dict]) -> str:
    """Sem modelo para resumir: lê o começo dos dois primeiros resultados."""
    return "Sem modelo disponível, mas achei isto na web: " + " ".join(
        f"{r['titulo']}. {r['trecho']}" for r in resultados[:2])


if __name__ == "__main__":  # teste rápido: python jarvis_web.py
    for pergunta in ("Qual o clima em Curitiba hoje?", "vai chover amanhã em são paulo?"):
        print(pergunta, "->", clima(pergunta))
    print(buscar("cotação do dólar hoje")[0])
