"""Voz do Jarvis (texto -> fala) com motores trocáveis e fallback automático.

Mesma ideia da cadeia do cérebro: tenta o primeiro motor e, se falhar, usa o próximo.
  1. ELEVENLABS -> voz da ElevenLabs, a mais humana; entende tags como [laughs].
                   Precisa de chave e gasta créditos (1 crédito ~ 1 caractere).
  2. EDGE       -> vozes neurais da Microsoft. Grátis, sem chave, precisa de internet.
  3. SISTEMA    -> pyttsx3 + vozes do Windows. Offline, porém mais robótica.

O texto vai para servidores de terceiros na ElevenLabs e no Edge.
Tags de emoção ([laughs], [whispers]...) só chegam à ElevenLabs; os outros
motores recebem o texto sem elas, para não lerem "laughs" em voz alta.

    pip install edge-tts pygame-ce pyttsx3

ElevenLabs (PowerShell):
    $env:ELEVENLABS_API_KEY="sua-chave"
    $env:ELEVENLABS_VOICE_ID="id-da-voz"      # em Voices > ... > Copy voice ID

Variáveis de ambiente (todas opcionais):
    JARVIS_MOTOR_VOZ   auto (elevenlabs, edge, sistema) | elevenlabs | edge | sistema
    ELEVENLABS_MODEL   modelo (padrão eleven_v3, o que entende as tags de emoção)
    ELEVENLABS_ESTABILIDADE  0.0 criativa | 0.5 natural | 1.0 estável (padrão 0.5)
    JARVIS_VOZ_EDGE    voz neural (padrão pt-BR-AntonioNeural; feminina: pt-BR-FranciscaNeural)
                       para ver todas:  edge-tts --list-voices
    JARVIS_RITMO_EDGE  velocidade da voz neural, ex.: "+10%" ou "-10%" (padrão -5%)
    JARVIS_TOM_EDGE    tom da voz neural, ex.: "-3Hz" (mais grave) ou "+2Hz" (padrão +0Hz)
    JARVIS_VOZ         parte do nome da voz do sistema (ex.: "maria")
    JARVIS_VELOCIDADE  palavras por minuto da voz do sistema (padrão 185)

Pronúncia de siglas/termos: edite PRONUNCIA_PADRAO ou crie pronuncia.json
ao lado deste arquivo, ex.: {"Kubernetes": "cubernétis", "SIGAWMS": "siga w m s"}

Uso (a API pública não mudou, o jarvis_hibrido.py continua igual):
    voz = Voz()
    voz.falar("Olá, eu sou o Jarvis.")   # não bloqueia: fala em segundo plano
    voz.calar()                          # interrompe a fala atual e limpa a fila
"""

import asyncio
import hashlib
import json
import os
import queue
import re
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

try:  # lê o .env (chaves e configurações) antes de qualquer os.getenv deste arquivo
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # sem python-dotenv: as variáveis precisam vir do sistema/terminal

VOZ_EDGE = os.getenv("JARVIS_VOZ_EDGE", "pt-BR-AntonioNeural")
RITMO_EDGE = os.getenv("JARVIS_RITMO_EDGE", "-5%")   # um pouco mais lento soa mais natural
TOM_EDGE = os.getenv("JARVIS_TOM_EDGE", "+0Hz")

PASTA_CACHE_VOZ = Path(__file__).with_name("cache_voz")


ELEVEN_URL = "https://api.elevenlabs.io/v1"
ELEVEN_MODELO = os.getenv("ELEVENLABS_MODEL", "eleven_v3")
ELEVEN_ESTABILIDADE = float(os.getenv("ELEVENLABS_ESTABILIDADE", "0.5"))

# [laughs], [whispers], [sighs]... (não confunde com link markdown, que tem "(" depois)
_TAG_EMOCAO = re.compile(r"\[[A-Za-zÀ-ú][\wÀ-ú '\-]{0,30}\](?!\()")


def remover_tags(texto: str) -> str:
    """Tira as tags de emoção: para exibir na tela e para motores que não as entendem."""
    texto = _TAG_EMOCAO.sub("", texto)
    texto = re.sub(r"[ \t]{2,}", " ", texto)
    return re.sub(r" +([,.!?;:])", r"\1", texto).strip()

MARCAS_PORTUGUES = ("brazil", "brasil", "portuguese", "português", "pt-br", "pt_br")


class VozIndisponivel(Exception):
    """Um motor de voz não pode falar agora (sem internet, sem pacote, sem áudio...)."""


# Como falar siglas e termos técnicos. Ajuste à vontade, ou crie um arquivo
# pronuncia.json ao lado deste ({"palavra": "como falar"}) para somar/substituir.
PRONUNCIA_PADRAO = {
    "API": "a pê í",
    "APIs": "a pê ís",
    "LLM": "éle éle ême",
    "RAG": "rég",
    "TF-IDF": "tê éfe, í dê éfe",
    "JSON": "djêison",
    "HTTP": "agá tê tê pê",
    "HTTPS": "agá tê tê pê ésse",
    "URL": "u érre éle",
    "SQL": "és quiú éle",
    "XSS": "xis ésse ésse",
    "CSRF": "cê ésse érre éfe",
    "OWASP": "ó uásp",
    "IA": "i á",
    "Ollama": "olâma",
    "Python": "páiton",
    "pip": "pípi",
    "e.g.": "por exemplo",
    "i.e.": "isto é",
    "etc.": "etcétera",
    "vs.": "versus",
    "e/ou": "e ou",
}


def carregar_pronuncia() -> dict[str, str]:
    pronuncia = dict(PRONUNCIA_PADRAO)
    arquivo = Path(__file__).with_name("pronuncia.json")
    if arquivo.exists():
        try:
            pronuncia.update(json.loads(arquivo.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass  # arquivo inválido: segue com o padrão
    return pronuncia


PRONUNCIA = carregar_pronuncia()
_PADRAO_PRONUNCIA = re.compile(
    r"(?<![\w.])(" + "|".join(re.escape(p) for p in sorted(PRONUNCIA, key=len, reverse=True)) + r")(?![\w])"
)
_EMOJIS = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF\uFE0F]")


# --------------------------------------------------------------------------
# Números e códigos (ex.: OWASP A01:2021)
# --------------------------------------------------------------------------
_UNIDADES = ["zero", "um", "dois", "três", "quatro", "cinco", "seis", "sete", "oito", "nove",
             "dez", "onze", "doze", "treze", "catorze", "quinze", "dezesseis", "dezessete",
             "dezoito", "dezenove"]
_DEZENAS = ["", "", "vinte", "trinta", "quarenta", "cinquenta", "sessenta", "setenta",
            "oitenta", "noventa"]
_CENTENAS = ["", "cento", "duzentos", "trezentos", "quatrocentos", "quinhentos", "seiscentos",
             "setecentos", "oitocentos", "novecentos"]
_LETRAS = dict(zip("ABCDEFGHIJKLMNOPQRSTUVWXYZ",
                   ["á", "bê", "cê", "dê", "é", "éfe", "gê", "agá", "í", "jota", "cá", "éle", "ême",
                    "ene", "ó", "pê", "quê", "érre", "ésse", "tê", "u", "vê", "dáblio", "xis",
                    "ípsilon", "zê"]))


def numero_por_extenso(n: int) -> str:
    """0 a 9999 por extenso, em pt-BR (2021 -> 'dois mil e vinte e um')."""
    if n < 20:
        return _UNIDADES[n]
    if n < 100:
        dezena, unidade = divmod(n, 10)
        return _DEZENAS[dezena] + (f" e {_UNIDADES[unidade]}" if unidade else "")
    if n == 100:
        return "cem"
    if n < 1000:
        centena, resto = divmod(n, 100)
        return _CENTENAS[centena] + (f" e {numero_por_extenso(resto)}" if resto else "")
    if n < 10000:
        milhar, resto = divmod(n, 1000)
        texto = "mil" if milhar == 1 else f"{_UNIDADES[milhar]} mil"
        if not resto:
            return texto
        # "dois mil e vinte" / "mil e quinhentos", mas "mil novecentos e oitenta"
        separador = " e " if resto < 100 or resto % 100 == 0 else " "
        return texto + separador + numero_por_extenso(resto)
    return str(n)


# Letras + número + ":" + ano. Ex.: A01:2021, API3:2023, LLM01:2025
_CODIGO_COM_ANO = re.compile(r"\b([A-Z]{1,4})(\d{1,3}):((?:19|20)\d{2})\b")


def _falar_codigo(m: re.Match) -> str:
    letras, numero, ano = m.groups()
    soletrado = " ".join(_LETRAS[letra] for letra in letras)
    if numero.startswith("0"):   # "01" -> "zero um"; "10" -> "dez", como se fala
        falado = " ".join(_UNIDADES[int(d)] for d in numero)
    else:
        falado = numero_por_extenso(int(numero))
    return f"{soletrado} {falado}, de {numero_por_extenso(int(ano))}"


def limpar_para_fala(texto: str, limite: int = 600) -> str:
    """Transforma texto de tela (markdown, listas, siglas) em texto de fala.

    O segredo das pausas: a voz neural só pausa onde há pontuação. Quebras de
    linha, títulos e itens de lista não têm pontuação, então viram frases
    corridas. Aqui cada linha vira uma frase terminada em ponto.
    """
    texto = re.sub(r"```.*?```", "\nColoquei um trecho de código na tela.\n", texto, flags=re.S)
    texto = _EMOJIS.sub("", texto)

    frases: list[str] = []
    for linha in texto.splitlines():
        linha = linha.strip()
        linha = re.sub(r"^#{1,6}\s*", "", linha)               # título
        linha = re.sub(r"^([-*•+]|\d+[.)])\s+", "", linha)     # item de lista
        linha = re.sub(r"^>\s*", "", linha)                    # citação
        if not linha or re.fullmatch(r"[-|:\s]+", linha):      # vazia ou separador de tabela
            continue
        linha = linha.strip("|").strip().replace("|", ",")     # célula de tabela vira pausa
        if not re.search(r"[.!?:;,…]$", linha):
            linha += "."
        frases.append(linha)
    texto = " ".join(frases)
    texto = _CODIGO_COM_ANO.sub(_falar_codigo, texto)            # A01:2021 -> á zero um, de dois mil...

    texto = re.sub(r"`([^`]*)`", r"\1", texto)                  # `codigo` -> codigo
    texto = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", texto)      # [texto](url) -> texto
    texto = re.sub(r"https?://\S+", "um link", texto)
    texto = re.sub(r"\s*(->|→|=>)\s*", ", depois, ", texto)
    texto = re.sub(r"\s*\(([^)]*)\)", r", \1,", texto)          # parênteses viram pausa
    texto = texto.replace("%", " por cento").replace("&", " e ")
    texto = _PADRAO_PRONUNCIA.sub(lambda m: PRONUNCIA[m.group(1)], texto)
    texto = re.sub(r"[*_#~>]", "", texto)                       # resto de markdown
    texto = re.sub(r"\s+([,.!?;:])", r"\1", texto)              # espaço antes de pontuação
    texto = re.sub(r",\s*([,.!?;:])", r"\1", texto)             # ", ." -> "."
    texto = re.sub(r"\s+", " ", texto).strip()

    if len(texto) > limite:                                     # resposta longa: lê o começo
        corte = max(texto.rfind(p, 0, limite) for p in ".!?")
        texto = texto[: corte + 1 if corte > 0 else limite] + " O resto está na tela."
    return texto


def escolher_voz(vozes: list, preferida: str | None):
    """Prioridade: nome pedido em JARVIS_VOZ > qualquer voz em português > padrão do sistema."""
    def descricao(voz) -> str:
        idiomas = " ".join(str(i) for i in (getattr(voz, "languages", None) or []))
        return f"{voz.id} {voz.name or ''} {idiomas}".lower()

    if preferida:
        for voz in vozes:
            if preferida.lower() in descricao(voz):
                return voz
    for voz in vozes:
        if any(marca in descricao(voz) for marca in MARCAS_PORTUGUES):
            return voz
    return None


# --------------------------------------------------------------------------
# Motores (Strategy): todos têm .descricao e .falar(texto, interromper)
# --------------------------------------------------------------------------
class MotorEdge:
    """Voz neural da Microsoft via edge-tts; o MP3 gerado é tocado com pygame."""

    nome = "edge"

    def __init__(self) -> None:
        self.voz, self.ritmo, self.tom = VOZ_EDGE, RITMO_EDGE, TOM_EDGE
        self.descricao = f"neural {self.voz}, online"
        try:
            import edge_tts
            os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")  # esconde o banner do pygame
            import pygame
        except ImportError as erro:
            raise VozIndisponivel(f"falta instalar: pip install edge-tts pygame ({erro.name})") from erro
        self._edge_tts = edge_tts
        self._pygame = pygame
        self._mixer_pronto = False

    def falar(self, texto: str, interromper: threading.Event) -> None:
        arquivo = Path(tempfile.gettempdir()) / f"jarvis_fala_{os.getpid()}.mp3"
        try:  # 1) sintetiza (rede)
            comunicacao = self._edge_tts.Communicate(texto, self.voz, rate=self.ritmo, pitch=self.tom)
            asyncio.run(comunicacao.save(str(arquivo)))
        except Exception as erro:  # sem internet, serviço fora do ar, voz inválida...
            raise VozIndisponivel(f"edge-tts: {erro}"[:120]) from erro
        if interromper.is_set():   # /calar chegou enquanto sintetizava
            return

        mixer = self._pygame.mixer
        try:  # 2) toca, checando /calar a cada 50 ms
            if not self._mixer_pronto:
                mixer.init()
                self._mixer_pronto = True
            mixer.music.load(str(arquivo))
            mixer.music.play()
            while mixer.music.get_busy():
                if interromper.wait(0.05):
                    mixer.music.stop()
                    break
            mixer.music.unload()  # solta o arquivo para poder apagar (Windows trava arquivos abertos)
        except self._pygame.error as erro:  # sem dispositivo de áudio, MP3 corrompido...
            raise VozIndisponivel(f"áudio: {erro}"[:120]) from erro
        finally:
            try:
                arquivo.unlink(missing_ok=True)
            except OSError:
                pass


class MotorSistema:
    """pyttsx3 com as vozes do Windows. Cria um motor novo a cada fala (bug do runAndWait)."""

    nome = "sistema"

    def __init__(self, preferida: str | None, velocidade: int) -> None:
        try:
            import pyttsx3
            motor = pyttsx3.init()  # só para descobrir as vozes
            escolhida = escolher_voz(motor.getProperty("voices") or [], preferida)
            motor.stop()
            del motor
        except Exception as erro:
            raise VozIndisponivel(f"pyttsx3: {erro}"[:120]) from erro
        self._pyttsx3 = pyttsx3
        self._velocidade = velocidade
        self._id_voz = escolhida.id if escolhida else None
        nome = escolhida.name if escolhida else "voz padrão, sem português instalado"
        self.descricao = f"sistema {nome}, offline"

    def falar(self, texto: str, interromper: threading.Event) -> None:
        try:
            motor = self._pyttsx3.init()
            motor.setProperty("rate", self._velocidade)
            if self._id_voz:
                motor.setProperty("voice", self._id_voz)

            def a_cada_palavra(*_args) -> None:  # roda nesta mesma thread: pode parar com segurança
                if interromper.is_set():
                    motor.stop()

            motor.connect("started-word", a_cada_palavra)
            motor.say(texto)
            motor.runAndWait()
            motor.stop()
            del motor  # libera o cache interno do pyttsx3
        except Exception as erro:
            raise VozIndisponivel(f"pyttsx3: {erro}"[:120]) from erro


def _requisitar_eleven(caminho: str, corpo: dict | None = None, timeout: float = 60) -> bytes:
    """Chamada HTTP à ElevenLabs só com a biblioteca padrão (mesmo estilo do post_json)."""
    chave = os.getenv("ELEVENLABS_API_KEY")
    if not chave:
        raise VozIndisponivel("ELEVENLABS_API_KEY não definida")
    requisicao = urllib.request.Request(
        f"{ELEVEN_URL}{caminho}",
        data=json.dumps(corpo).encode("utf-8") if corpo is not None else None,
        headers={"xi-api-key": chave, "Content-Type": "application/json",
                 "Accept": "audio/mpeg" if corpo is not None else "application/json"},
        method="POST" if corpo is not None else "GET",
    )
    try:
        with urllib.request.urlopen(requisicao, timeout=timeout) as resposta:
            return resposta.read()
    except urllib.error.HTTPError as erro:
        detalhe = erro.read().decode("utf-8", errors="replace")
        try:  # erros da ElevenLabs vêm como {"detail": {"message": ...}} ou {"detail": "..."}
            info = json.loads(detalhe)["detail"]
            detalhe = info.get("message", info) if isinstance(info, dict) else info
        except (ValueError, KeyError, TypeError):
            pass
        raise VozIndisponivel(f"ElevenLabs HTTP {erro.code}: {detalhe}"[:160]) from erro
    except (OSError, ValueError) as erro:  # sem internet, timeout...
        raise VozIndisponivel(f"ElevenLabs: {erro}"[:160]) from erro


def _audio_elevenlabs(texto: str) -> tuple[Path, bool]:
    """MP3 da fala, com cache em disco: cada caractere enviado gasta crédito.

    Devolve (arquivo, veio_do_cache). Texto repetido nunca é cobrado duas vezes.
    """
    voz = os.getenv("ELEVENLABS_VOICE_ID")
    if not voz:
        raise VozIndisponivel("ELEVENLABS_VOICE_ID não definida")
    assinatura = f"eleven|{voz}|{ELEVEN_MODELO}|{ELEVEN_ESTABILIDADE}|{texto}"
    arquivo = PASTA_CACHE_VOZ / f"{hashlib.sha1(assinatura.encode('utf-8')).hexdigest()}.mp3"
    if arquivo.exists():
        return arquivo, True

    audio = _requisitar_eleven(f"/text-to-speech/{voz}?output_format=mp3_44100_128", {
        "text": texto,
        "model_id": ELEVEN_MODELO,
        "voice_settings": {"stability": ELEVEN_ESTABILIDADE, "similarity_boost": 0.75},
    })
    if not audio:
        raise VozIndisponivel("ElevenLabs devolveu áudio vazio")
    PASTA_CACHE_VOZ.mkdir(exist_ok=True)
    temporario = arquivo.with_suffix(".tmp")
    temporario.write_bytes(audio)
    temporario.replace(arquivo)  # escrita atômica
    return arquivo, False


class MotorElevenLabs:
    """Voz da ElevenLabs via API REST; o MP3 fica em cache e é tocado com pygame."""

    nome = "elevenlabs"
    aceita_tags = True  # a Voz só manda [laughs] etc. para motores que declaram isso

    def __init__(self) -> None:
        if not os.getenv("ELEVENLABS_API_KEY") or not os.getenv("ELEVENLABS_VOICE_ID"):
            raise VozIndisponivel("defina ELEVENLABS_API_KEY e ELEVENLABS_VOICE_ID")
        try:
            os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
            import pygame
        except ImportError as erro:
            raise VozIndisponivel("falta instalar: pip install pygame-ce") from erro
        self._pygame = pygame
        self.restantes: int | None = None
        try:  # só informativo: chaves com permissão restrita podem não ler a assinatura
            dados = json.loads(_requisitar_eleven("/user/subscription", timeout=5))
            self.restantes = int(dados["character_limit"]) - int(dados["character_count"])
        except (VozIndisponivel, KeyError, ValueError, TypeError):
            pass

    @property
    def descricao(self) -> str:
        creditos = f", ~{self.restantes} créditos" if self.restantes is not None else ""
        return f"elevenlabs {ELEVEN_MODELO}{creditos}, online"

    def falar(self, texto: str, interromper: threading.Event) -> None:
        arquivo, do_cache = _audio_elevenlabs(texto)
        if not do_cache and self.restantes is not None:
            self.restantes -= len(texto)
        if interromper.is_set():
            return
        mixer = self._pygame.mixer
        try:
            if not mixer.get_init():
                mixer.init()
            mixer.music.load(str(arquivo))
            mixer.music.play()
            while mixer.music.get_busy():
                if interromper.wait(0.05):
                    mixer.music.stop()
                    break
            mixer.music.unload()
        except self._pygame.error as erro:
            raise VozIndisponivel(f"áudio: {erro}"[:120]) from erro


def status_elevenlabs() -> dict:
    """Situação da conta ElevenLabs para exibir na interface (nunca levanta exceção).

    Chaves: ok (bool), mensagem (str) e, quando a assinatura pôde ser lida,
    plano, usados, limite, restantes e renovacao (timestamp unix ou None).
    """
    if not os.getenv("ELEVENLABS_API_KEY") or not os.getenv("ELEVENLABS_VOICE_ID"):
        return {"ok": False, "mensagem": "não configurada (faltam ELEVENLABS_API_KEY/ELEVENLABS_VOICE_ID no .env)"}
    try:
        dados = json.loads(_requisitar_eleven("/user/subscription", timeout=8))
        usados, limite = int(dados["character_count"]), int(dados["character_limit"])
    except VozIndisponivel as erro:
        if "user_read" in str(erro):
            return {"ok": True, "mensagem": "API conectada, mas a chave não pode ler os créditos "
                    "(ative a permissão user_read na chave, em Developers > API Keys)"}
        return {"ok": False, "mensagem": str(erro)}
    except (KeyError, ValueError, TypeError):
        return {"ok": False, "mensagem": "resposta inesperada da ElevenLabs"}
    return {"ok": True, "mensagem": "API conectada", "plano": dados.get("tier", "?"),
            "usados": usados, "limite": limite, "restantes": max(limite - usados, 0),
            "renovacao": dados.get("next_character_count_reset_unix")}


def gerar_mp3(texto: str) -> bytes | None:
    """Gera o MP3 da fala (ElevenLabs, se configurada; senão Edge), SEM tocar.

    Para interfaces que tocam o áudio por conta própria (ex.: Streamlit, no navegador).
    Devolve None se não houver nada para falar; levanta VozIndisponivel se falhar.
    """
    limpo = limpar_para_fala(texto)
    if not limpo:
        return None
    if os.getenv("ELEVENLABS_API_KEY") and os.getenv("ELEVENLABS_VOICE_ID"):
        try:
            return _audio_elevenlabs(limpo)[0].read_bytes()
        except VozIndisponivel:
            pass  # sem créditos, sem internet...: cai para a voz neural gratuita
    limpo = remover_tags(limpo)
    if not limpo:
        return None
    try:
        import edge_tts
    except ImportError as erro:
        raise VozIndisponivel("falta instalar: pip install edge-tts") from erro

    async def baixar() -> bytes:
        partes = []
        async for bloco in edge_tts.Communicate(limpo, VOZ_EDGE, rate=RITMO_EDGE, pitch=TOM_EDGE).stream():
            if bloco["type"] == "audio":
                partes.append(bloco["data"])
        return b"".join(partes)

    try:  # thread própria: asyncio.run não briga com event loop de quem chamou
        with ThreadPoolExecutor(max_workers=1) as executor:
            return executor.submit(asyncio.run, baixar()).result(timeout=60) or None
    except Exception as erro:
        raise VozIndisponivel(f"edge-tts: {erro}"[:120]) from erro


# --------------------------------------------------------------------------
# Voz: fila + thread + cadeia de motores
# --------------------------------------------------------------------------
class Voz:
    """Fala numa thread própria, alimentada por uma fila (produtor/consumidor).

    Os motores são criados e usados SEMPRE na thread de trabalho, porque nem
    o pyttsx3 (COM) nem o mixer do pygame gostam de trocar de thread.
    """

    def __init__(self, nome_voz: str | None = None, velocidade: int | None = None) -> None:
        self.ativa = True
        self.disponivel = False
        self.erros_inicio: list[str] = []  # motores que nem subiram (sem chave, sem pacote...)
        self.erro = ""                     # falhas na última fala, motor por motor
        self.motores: list = []
        self._preferida = nome_voz or os.getenv("JARVIS_VOZ")
        self._velocidade = velocidade or int(os.getenv("JARVIS_VELOCIDADE", "185"))
        self._modo = os.getenv("JARVIS_MOTOR_VOZ", "auto").strip().lower()
        self._fila: queue.Queue[str | None] = queue.Queue()
        self._interromper = threading.Event()
        self._pronta = threading.Event()
        self._thread = threading.Thread(target=self._trabalhar, daemon=True)
        self._thread.start()
        self._pronta.wait(timeout=15)  # espera os motores subirem (ou falharem)

    # ---- API pública -------------------------------------------------
    def falar(self, texto: str) -> None:
        # Ainda iniciando? Enfileira mesmo assim: a thread fala assim que os motores subirem.
        if self.ativa and (self.disponivel or not self._pronta.is_set()):
            limpo = limpar_para_fala(texto)
            if limpo:
                self._fila.put(limpo)

    def calar(self) -> None:
        """Descarta as falas pendentes e interrompe a que está tocando."""
        while True:
            try:
                self._fila.get_nowait()
            except queue.Empty:
                break
        self._interromper.set()

    def encerrar(self, esperar: float = 30) -> None:
        """Termina de falar o que está na fila e só então libera o programa para fechar.

        A thread é daemon: se o programa principal acabar antes, ela morre no meio da frase.
        """
        self._fila.put(None)
        self._thread.join(timeout=esperar)

    def status(self) -> str:
        if not self._pronta.is_set():
            return "voz: iniciando os motores..."
        if not self.disponivel:
            return f"voz: indisponível ({'; '.join(self.erros_inicio) or 'nenhum motor instalado'})"
        cadeia = " -> ".join(m.descricao for m in self.motores)
        texto = f"voz: {'ligada' if self.ativa else 'desligada'} ({cadeia})"
        if self.erros_inicio:
            texto += "\n  não iniciaram: " + "; ".join(self.erros_inicio)
        if self.erro:
            texto += f"\n  falharam na última fala: {self.erro}"
        return texto

    # ---- thread de trabalho ------------------------------------------
    def _criar_motores(self) -> None:
        ordens = {"elevenlabs": ["elevenlabs"], "edge": ["edge"], "sistema": ["sistema"]}
        ordem = ordens.get(self._modo, ["elevenlabs", "edge", "sistema"])
        falhas: list[str] = []
        for nome in ordem:
            try:
                if nome == "elevenlabs":
                    self.motores.append(MotorElevenLabs())
                elif nome == "edge":
                    self.motores.append(MotorEdge())
                else:
                    self.motores.append(MotorSistema(self._preferida, self._velocidade))
            except VozIndisponivel as erro:
                falhas.append(f"{nome}: {erro}")
        self.erros_inicio = falhas

    def _trabalhar(self) -> None:
        try:
            if sys.platform == "win32":  # SAPI5 é COM: precisa inicializar nesta thread
                try:
                    import comtypes
                    comtypes.CoInitialize()
                except ImportError:
                    pass  # sem pyttsx3 instalado; o motor de sistema vai acusar sozinho
            self._criar_motores()
            self.disponivel = bool(self.motores)
        finally:
            self._pronta.set()
        if not self.disponivel:
            return

        while (texto := self._fila.get()) is not None:
            self._interromper.clear()
            falhas: list[str] = []
            for motor in self.motores:  # cadeia: o primeiro que conseguir, fala
                texto_motor = texto if getattr(motor, "aceita_tags", False) else remover_tags(texto)
                if not texto_motor:  # a fala era só uma tag, ex.: "[laughs]"
                    break
                try:
                    motor.falar(texto_motor, self._interromper)
                    break
                except VozIndisponivel as erro:
                    falhas.append(f"{motor.nome}: {erro}")
            self.erro = "; ".join(falhas)


if __name__ == "__main__":  # teste rápido: python jarvis_voz.py
    voz = Voz()
    print(voz.status())
    voz.falar("Olá! Eu sou a Jarvis, sua assistente pessoal aqui na Guaraves. O que gostaria de fazer hoje?")
    voz.falar("[excited] Consegui! Os números já estão aqui. [laughs] Eu sabia que ia dar certo.")
    voz.encerrar(esperar=120)
    print(voz.status())