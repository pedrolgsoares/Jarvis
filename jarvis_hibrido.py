"""Jarvis híbrido: junta tudo o que construímos, com fallback automático.

Cadeia de respostas (para na primeira que resolver):
  1. BASE      -> frases que você ensinou (conhecimento.json, busca TF-IDF)
  2. CLIMA     -> perguntas de tempo/temperatura/chuva vão direto à Open-Meteo (jarvis_web.py)
  3. DOCS      -> RAG: acha trechos nas suas anotações (pasta docs/, arquivos .txt e .md)
  4. WEB       -> notícias, cotações e "pesquise...": busca no DuckDuckGo e o modelo resume
  5. LLM LOCAL -> Ollama rodando na sua máquina (sem chave, sem internet)
  6. LLM API   -> só se o local falhar E existir ANTHROPIC_API_KEY
  7. WEB (plano B) -> sem modelo nem anotações, lê o começo dos resultados da busca
  8. APRENDER  -> se nada acima estiver disponível, pede para você ensinar
  9. ELIZA     -> último recurso, para nunca ficar mudo

Enquanto pensa, um spinner mostra a etapa atual e o tempo decorrido, ex.:
  ⠹ perguntando ao Ollama (llama3.2) com 2 trecho(s) de notas.md (3.4s)

Depois de responder, o Jarvis fala a resposta em voz alta (jarvis_voz.py).

Requisitos:
    pip install scikit-learn pyttsx3
    (opcional) Ollama instalado + um modelo:  ollama pull llama3.2
    (opcional) $env:ANTHROPIC_API_KEY="sua-chave"     # PowerShell

Coloque este arquivo na MESMA pasta de jarvis_classico.py, jarvis_tfidf.py e
jarvis_eliza.py e jarvis_voz.py. Coloque suas anotações (.txt/.md) na pasta
"docs" ao lado dele.

Variáveis de ambiente (todas opcionais):
    OLLAMA_URL         (padrão http://localhost:11434)
    OLLAMA_MODEL       (padrão llama3.2)
    ANTHROPIC_API_KEY  (sem ela, o fallback de API fica desligado)
    ANTHROPIC_MODEL    (padrão claude-sonnet-5-5)
    JARVIS_DOCS_NA_API (1 = permite enviar trechos dos seus docs para a API; padrão: NÃO)
    JARVIS_VOZ         (parte do nome da voz, ex.: "maria")
    JARVIS_VELOCIDADE  (palavras por minuto da fala; padrão 185)

Comandos: /ajuda
"""

import itertools
import json
import os
import random
import re
import shutil
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from jarvis_classico import carregar, normalizar, salvar
from jarvis_eliza import Eliza
from jarvis_tfidf import STOPWORDS, Buscador
import jarvis_web
from jarvis_voz import Voz  # [VOZ] módulo de fala
from jarvis_web import WebIndisponivel
from dotenv import load_dotenv
load_dotenv()  # lê o .env antes de qualquer módulo consultar as variáveis
# --------------------------------------------------------------------------
# Configuração
# --------------------------------------------------------------------------
PASTA_DOCS = Path(__file__).with_name("docs")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODELO = os.getenv("OLLAMA_MODEL", "llama3.2")
API_URL = "https://api.anthropic.com/v1/messages"
API_MODELO = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5")
DOCS_NA_API = os.getenv("JARVIS_DOCS_NA_API") == "1"

LIMIAR_BASE = 0.5   # similaridade mínima para responder direto da base aprendida
LIMIAR_DOCS = 0.15  # similaridade mínima para um trecho de documento ser considerado
TOP_TRECHOS = 3     # quantos trechos entram no contexto
MAX_MENSAGENS = 20  # histórico enviado ao modelo a cada chamada
TAMANHO_TRECHO = 700

SYSTEM_PROMPT = (
    "Você é a Jarvis, assistente pessoal da Guaraves: inteligente, educada e objetiva. "
    "Responda sempre em português do Brasil, de forma concisa. "
    "Suas respostas serão lidas em voz alta: use frases curtas e naturais, como numa conversa, "
    "sem listas, tabelas, emojis ou markdown. "
    "Se não souber algo, diga que não sabe em vez de inventar."
)


class Indisponivel(Exception):
    """Uma fonte de resposta não está disponível agora (offline, sem chave, erro...)."""


# --------------------------------------------------------------------------
# Spinner (indicador de progresso no terminal)
# --------------------------------------------------------------------------
class Spinner:
    """Animação numa thread separada, usada como context manager.

        with spinner:
            spinner.etapa("fazendo algo")   # troca o texto exibido
            spinner.log("mensagem fixa")    # imprime sem bagunçar a animação
            ...                             # trabalho bloqueante (HTTP etc.)

    Se a saída não for um terminal (ex.: redirecionada para arquivo),
    não anima: só imprime cada etapa numa linha.
    """

    QUADROS_UNICODE = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    QUADROS_ASCII = "|/-\\"

    def __init__(self, intervalo: float = 0.1, saida=sys.stderr) -> None:
        self.saida = saida
        self.intervalo = intervalo
        self.animado = hasattr(saida, "isatty") and saida.isatty()
        try:  # console antigo do Windows pode não ter os caracteres braille
            self.QUADROS_UNICODE.encode(saida.encoding or "ascii")
            self.quadros = self.QUADROS_UNICODE
        except (UnicodeEncodeError, LookupError):
            self.quadros = self.QUADROS_ASCII
        self.mensagem = "pensando"
        self._lock = threading.Lock()
        self._parar = threading.Event()
        self._thread: threading.Thread | None = None
        self._inicio = 0.0
        self._largura = 0  # tamanho da última linha desenhada (para apagar)

    # ---- context manager ---------------------------------------------
    def __enter__(self) -> "Spinner":
        self._inicio = time.monotonic()
        self.mensagem = "pensando"
        self._parar.clear()
        if self.animado:
            self._thread = threading.Thread(target=self._girar, daemon=True)
            self._thread.start()
        return self

    def __exit__(self, *_exc) -> bool:
        self._parar.set()
        if self._thread:
            self._thread.join()
            self._thread = None
        with self._lock:
            self._apagar()
        return False  # não engole exceções (Ctrl+C continua funcionando)

    # ---- API pública -------------------------------------------------
    def etapa(self, mensagem: str) -> None:
        with self._lock:
            self.mensagem = mensagem
        if not self.animado:
            print(f"  ... {mensagem}", file=self.saida, flush=True)

    def log(self, texto: str) -> None:
        """Imprime uma linha permanente; o spinner volta a ser desenhado embaixo dela."""
        with self._lock:
            self._apagar()
            print(texto, file=self.saida, flush=True)

    # ---- internos ----------------------------------------------------
    def _girar(self) -> None:
        for quadro in itertools.cycle(self.quadros):
            with self._lock:
                decorrido = time.monotonic() - self._inicio
                self._desenhar(f"  {quadro} {self.mensagem} ({decorrido:.1f}s)")
            if self._parar.wait(self.intervalo):
                break

    def _desenhar(self, linha: str) -> None:
        # nunca deixar a linha quebrar, senão o \r não consegue voltar
        maximo = shutil.get_terminal_size((80, 20)).columns - 1
        if len(linha) > maximo:
            linha = linha[: maximo - 1] + "…"
        self.saida.write("\r" + linha.ljust(self._largura))
        self.saida.flush()
        self._largura = len(linha)

    def _apagar(self) -> None:
        if self.animado and self._largura:
            self.saida.write("\r" + " " * self._largura + "\r")
            self.saida.flush()
            self._largura = 0


# --------------------------------------------------------------------------
# HTTP mínimo (só biblioteca padrão)
# --------------------------------------------------------------------------
def post_json(url: str, corpo: dict, headers: dict | None = None, timeout: int = 120) -> dict:
    requisicao = urllib.request.Request(
        url,
        data=json.dumps(corpo).encode("utf-8"),
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )
    try:
        with urllib.request.urlopen(requisicao, timeout=timeout) as resposta:
            return json.loads(resposta.read().decode("utf-8"))
    except urllib.error.HTTPError as erro:
        detalhe = erro.read().decode("utf-8", errors="replace")[:200]
        raise Indisponivel(f"HTTP {erro.code}: {detalhe}") from erro
    except (OSError, ValueError) as erro:  # URLError, timeout, conexão recusada, JSON inválido
        raise Indisponivel(str(erro)) from erro


def ollama_ativo() -> bool:
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=2):
            return True
    except (OSError, ValueError):
        return False


# --------------------------------------------------------------------------
# RAG: busca em documentos
# --------------------------------------------------------------------------
def dividir(texto: str, limite: int = TAMANHO_TRECHO) -> list[str]:
    """Quebra o texto em trechos de até ~limite caracteres, respeitando parágrafos."""
    pedacos: list[str] = []
    for paragrafo in re.split(r"\n\s*\n", texto):
        paragrafo = paragrafo.strip()
        while len(paragrafo) > limite:  # parágrafo gigante: corta no último espaço
            corte = paragrafo.rfind(" ", 0, limite)
            corte = corte if corte > 0 else limite
            pedacos.append(paragrafo[:corte].strip())
            paragrafo = paragrafo[corte:].strip()
        if paragrafo:
            pedacos.append(paragrafo)

    trechos: list[str] = []
    atual = ""
    for pedaco in pedacos:
        if atual and len(atual) + len(pedaco) + 2 > limite:
            trechos.append(atual)
            atual = pedaco
        else:
            atual = f"{atual}\n\n{pedaco}".strip()
    if atual:
        trechos.append(atual)
    return trechos


class Documentos:
    """Indexa os .txt/.md da pasta docs/ com TF-IDF e devolve os trechos mais relevantes."""

    def __init__(self, pasta: Path) -> None:
        self.pasta = pasta
        self.carregar()

    def carregar(self) -> None:
        self.trechos: list[tuple[str, str]] = []  # (nome_do_arquivo, texto)
        self.vetorizador = None
        self.matriz = None
        self.pasta.mkdir(exist_ok=True)

        for arquivo in sorted(self.pasta.rglob("*")):
            if arquivo.is_file() and arquivo.suffix.lower() in (".txt", ".md"):
                texto = arquivo.read_text(encoding="utf-8", errors="ignore")
                self.trechos += [(arquivo.name, t) for t in dividir(texto)]

        if not self.trechos:
            return
        vetorizador = TfidfVectorizer(stop_words=STOPWORDS, sublinear_tf=True)
        try:
            self.matriz = vetorizador.fit_transform(normalizar(t) for _, t in self.trechos)
            self.vetorizador = vetorizador
        except ValueError:  # vocabulário vazio
            self.vetorizador = None

    def buscar(self, pergunta_normalizada: str, k: int = TOP_TRECHOS) -> list[tuple[str, str, float]]:
        if self.vetorizador is None:
            return []
        vetor = self.vetorizador.transform([pergunta_normalizada])
        if vetor.nnz == 0:
            return []
        scores = cosine_similarity(vetor, self.matriz)[0]
        melhores = scores.argsort()[::-1][:k]
        return [(*self.trechos[i], float(scores[i])) for i in melhores if scores[i] >= LIMIAR_DOCS]


def montar_prompt(pergunta: str, trechos: list[tuple[str, str, float]]) -> str:
    blocos = "\n\n".join(f"[trecho {i} - {arquivo}]\n{texto}" for i, (arquivo, texto, _) in enumerate(trechos, 1))
    return (
        "Use os trechos abaixo, das anotações do usuário, para responder. "
        "Se não forem relevantes, responda normalmente e avise que não achou nas anotações.\n\n"
        f"{blocos}\n\nPergunta: {pergunta}"
    )


def descrever_trechos(trechos: list[tuple[str, str, float]]) -> str:
    """Ex.: '2 trecho(s) de notas.md, python.md'"""
    arquivos = ", ".join(dict.fromkeys(arquivo for arquivo, _, _ in trechos))  # sem repetir, na ordem
    return f"{len(trechos)} trecho(s) de {arquivos}"


# --------------------------------------------------------------------------
# O Jarvis
# --------------------------------------------------------------------------
class Jarvis:
    def __init__(self) -> None:
        self.base = carregar()
        self.buscador = Buscador(self.base)
        self.docs = Documentos(PASTA_DOCS)
        self.eliza = Eliza()
        self.historico: list[dict] = []
        self.modo = "auto"  # auto = local e depois API | local | api
        self.ultima: tuple[str, str, str] | None = None  # (pergunta, resposta, fonte)
        self.avisou_docs = False

        # Observers: quem usa o Jarvis decide como exibir progresso e avisos.
        # Por padrão, progresso é ignorado e avisos vão para o print.
        self.ao_progresso: Callable[[str], None] = lambda _mensagem: None
        self.ao_avisar: Callable[[str], None] = print

    # ---- memória de conversa -------------------------------------------
    def janela(self) -> list[dict]:
        recorte = self.historico[-MAX_MENSAGENS:]
        while recorte and recorte[0]["role"] != "user":  # a API exige começar por 'user'
            recorte = recorte[1:]
        return recorte

    def registrar(self, pergunta: str, resposta: str, fonte: str) -> None:
        self.historico += [{"role": "user", "content": pergunta}, {"role": "assistant", "content": resposta}]
        self.ultima = (pergunta, resposta, fonte)

    def guardar(self) -> None:
        """Transforma a última resposta gerada em conhecimento permanente da base."""
        pergunta, resposta, _ = self.ultima
        self.base.setdefault(normalizar(pergunta), []).append(resposta)
        salvar(self.base)
        self.buscador.reconstruir(self.base)

    # ---- cérebros generativos ------------------------------------------
    def llm_local(self, mensagens: list[dict]) -> str:
        resposta = post_json(
            f"{OLLAMA_URL}/api/chat",
            {"model": OLLAMA_MODELO, "stream": False,
             "messages": [{"role": "system", "content": SYSTEM_PROMPT}] + mensagens},
        )
        try:
            texto = resposta["message"]["content"].strip()
        except (KeyError, TypeError, AttributeError) as erro:
            raise Indisponivel(f"resposta inesperada do Ollama: {str(resposta)[:100]}") from erro
        if not texto:
            raise Indisponivel("resposta vazia do Ollama")
        return texto

    def llm_api(self, mensagens: list[dict]) -> str:
        chave = os.getenv("ANTHROPIC_API_KEY")
        if not chave:
            raise Indisponivel("ANTHROPIC_API_KEY não definida")
        resposta = post_json(
            API_URL,
            {"model": API_MODELO, "max_tokens": 1024, "system": SYSTEM_PROMPT, "messages": mensagens},
            headers={"x-api-key": chave, "anthropic-version": "2023-06-01"},
        )
        try:
            texto = "".join(b["text"] for b in resposta["content"] if b.get("type") == "text").strip()
        except (KeyError, TypeError) as erro:
            raise Indisponivel(f"resposta inesperada da API: {str(resposta)[:100]}") from erro
        if not texto:
            raise Indisponivel("resposta vazia da API")
        return texto

    def perguntar_llm(self, pergunta: str, trechos: list[tuple[str, str, float]],
                      contexto_web: str = "") -> tuple[str, str]:
        funcoes = {"local": self.llm_local, "api": self.llm_api}
        nomes = {"local": f"ao Ollama ({OLLAMA_MODELO})", "api": f"à API ({API_MODELO})"}
        ordem = {"auto": ["local", "api"], "local": ["local"], "api": ["api"]}[self.modo]
        falhas: list[str] = []

        for nome in ordem:
            if nome == "api" and trechos and not DOCS_NA_API and not self.avisou_docs:
                self.ao_avisar("  [aviso: seus documentos NÃO são enviados à API. Para permitir: JARVIS_DOCS_NA_API=1]")
                self.avisou_docs = True
            usa_docs = bool(trechos) and (nome == "local" or DOCS_NA_API) and not contexto_web
            if contexto_web:  # resultados públicos da web: podem ir para qualquer modelo
                prompt = f"{contexto_web}\n\nPergunta: {pergunta}"
            else:
                prompt = montar_prompt(pergunta, trechos) if usa_docs else pergunta
            mensagens = self.janela() + [{"role": "user", "content": prompt}]

            contexto = (" com resultados da web" if contexto_web
                        else f" com {descrever_trechos(trechos)}" if usa_docs else "")
            self.ao_progresso(f"perguntando {nomes[nome]}{contexto}")
            try:
                return funcoes[nome](mensagens), f"llm-{nome}"
            except Indisponivel as erro:
                falhas.append(f"{nome}: {erro}")
                if nome != ordem[-1]:
                    self.ao_avisar(f"  [{nome} indisponível ({str(erro)[:70]}); tentando o próximo...]")

        raise Indisponivel("; ".join(falhas))

    # ---- a cadeia completa ---------------------------------------------
    def responder(self, entrada: str) -> tuple[str, str] | None:
        """Etapas automáticas (1 a 4 + trecho cru). Devolve None se nada resolveu.

        Não pede nada ao usuário aqui dentro: assim pode rodar inteira
        com o spinner girando, sem brigar com um input().
        """
        chave = normalizar(entrada)

        # 1) base aprendida
        self.ao_progresso("procurando na base aprendida")
        achada, score = self.buscador.buscar(chave)
        if achada and score >= LIMIAR_BASE and self.base.get(achada):
            resposta = random.choice(self.base[achada])
            self.registrar(entrada, resposta, "base")
            return resposta, "base"

        # 2) clima: dado ao vivo, o modelo não tem como saber (Open-Meteo)
        if jarvis_web.pergunta_de_clima(entrada):
            self.ao_progresso("consultando a previsão do tempo")
            try:
                resposta = jarvis_web.clima(entrada)
                self.registrar(entrada, resposta, "web-clima")
                return resposta, "web-clima"
            except WebIndisponivel as erro:
                self.ao_avisar(f"  [clima indisponível: {str(erro)[:100]}]")

        # 3) documentos (viram contexto para o modelo)
        self.ao_progresso(f"buscando nas anotações ({PASTA_DOCS.name}/)")
        trechos = self.docs.buscar(chave)

        # 4) pergunta sobre algo recente (notícia, cotação...): busca antes de perguntar ao modelo
        resultados_web: list[dict] = []
        if jarvis_web.pergunta_atual(entrada):
            resultados_web = self.buscar_na_web(entrada)

        # 5 e 6) modelo local, depois API
        try:
            contexto_web = jarvis_web.formatar_contexto(resultados_web) if resultados_web else ""
            resposta, fonte = self.perguntar_llm(entrada, trechos, contexto_web)
            if resultados_web:
                fonte = "web+" + fonte
            self.registrar(entrada, resposta, fonte)
            return resposta, fonte
        except Indisponivel as erro:
            self.ao_avisar(f"  [sem cérebro generativo: {str(erro)[:160]}]")

        # sem modelo: se houver documento relevante, mostra o trecho cru
        if trechos:
            arquivo, texto, _ = trechos[0]
            resposta = f"Sem modelo disponível, mas achei isto em {arquivo}:\n\n{texto}"
            self.registrar(entrada, resposta, "docs")
            return resposta, "docs"

        # último recurso antes de pedir ensino: a web (se ainda não tentou)
        resultados_web = resultados_web or self.buscar_na_web(entrada)
        if resultados_web:
            resposta = jarvis_web.resumo_cru(resultados_web)
            self.registrar(entrada, resposta, "web")
            return resposta, "web"

        return None

    def buscar_na_web(self, pergunta: str) -> list[dict]:
        """Busca no DuckDuckGo; falha vira aviso e lista vazia, nunca exceção."""
        self.ao_progresso("pesquisando na web")
        try:
            return jarvis_web.buscar(pergunta)
        except WebIndisponivel as erro:
            self.ao_avisar(f"  [web indisponível: {str(erro)[:100]}]")
            return []

    def aprender_ou_eliza(self, entrada: str) -> tuple[str, str]:
        """Etapas 5 e 6: interativas, por isso rodam fora do spinner."""
        print("Jarvis [aprender]: Não sei isso e estou sem modelo. O que eu deveria dizer?")
        ensino = input("Ensinar (Enter para pular): ").strip()
        if ensino:
            self.base.setdefault(normalizar(entrada), []).append(ensino)
            salvar(self.base)
            self.buscador.reconstruir(self.base)
            self.registrar(entrada, ensino, "ensinado")
            return "Aprendi! Da próxima vez eu sei responder.", "aprender"

        resposta = self.eliza.responder(entrada)
        self.registrar(entrada, resposta, "eliza")
        return resposta, "eliza"

    def status(self) -> str:
        return (
            f"modo: {self.modo}\n"
            f"base aprendida: {len(self.base)} frases\n"
            f"documentos: {len(self.docs.trechos)} trechos em {PASTA_DOCS.name}/\n"
            f"Ollama ({OLLAMA_URL}, modelo {OLLAMA_MODELO}): {'ativo' if ollama_ativo() else 'inativo'}\n"
            f"API ({API_MODELO}): {'chave definida' if os.getenv('ANTHROPIC_API_KEY') else 'sem chave'}"
            f"{' | docs liberados para a API' if DOCS_NA_API else ' | docs NÃO enviados à API'}"
        )


AJUDA = """Comandos:
  /status      mostra o que está disponível agora (Ollama, API, base, docs, voz)
  /modo X      X = auto (local e depois API) | local | api
  /voz on|off  liga ou desliga a fala (sem argumento: mostra o estado)
  /calar       para de falar as respostas que estavam na fila
  /guardar     salva a última resposta do modelo/docs na base aprendida
  /recarregar  relê a pasta docs/
  /limpar      apaga o histórico da conversa atual
  /sair        encerra"""


def main() -> None:
    jarvis = Jarvis()
    spinner = Spinner()
    jarvis.ao_progresso = spinner.etapa  # liga o Observer ao spinner
    jarvis.ao_avisar = spinner.log

    print("Jarvis híbrido online. /ajuda para ver os comandos.")
    with spinner:
        spinner.etapa("preparando a voz")  # [VOZ] o motor leva 1-2s para subir
        voz = Voz()
        spinner.etapa("verificando Ollama e API")
        status = jarvis.status()
    print(status)
    print(voz.status())  # [VOZ]

    while True:
        try:
            entrada = input("\nVocê: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nAté logo!")
            break
        if not entrada:
            continue

        comando, _, argumento = entrada.lower().partition(" ")
        if comando in ("/sair", "sair"):
            print("Até logo!")
            break
        if comando == "/ajuda":
            print(AJUDA)
            continue
        if comando == "/status":
            with spinner:
                spinner.etapa("verificando Ollama e API")
                status = jarvis.status()
            print(status)
            print(voz.status())  # [VOZ]
            continue
        if comando == "/modo":
            if argumento.strip() in ("auto", "local", "api"):
                jarvis.modo = argumento.strip()
                print(f"Jarvis: modo {jarvis.modo}.")
            else:
                print("Jarvis: use /modo auto, /modo local ou /modo api.")
            continue
        if comando == "/voz":  # [VOZ]
            if argumento.strip() in ("on", "off"):
                voz.ativa = argumento.strip() == "on"
                if not voz.ativa:
                    voz.calar()
            print(f"Jarvis: {voz.status()}")
            continue
        if comando == "/calar":  # [VOZ]
            voz.calar()
            print("Jarvis: ok, parei.")
            continue
        if comando == "/recarregar":
            with spinner:
                spinner.etapa(f"reindexando {PASTA_DOCS.name}/")
                jarvis.docs.carregar()
            print(f"Jarvis: {len(jarvis.docs.trechos)} trechos indexados.")
            continue
        if comando == "/limpar":
            jarvis.historico.clear()
            print("Jarvis: histórico apagado.")
            continue
        if comando == "/guardar":
            if jarvis.ultima and jarvis.ultima[2] in ("llm-local", "llm-api", "docs"):
                jarvis.guardar()
                print("Jarvis: guardado! Da próxima vez respondo direto da base.")
            else:
                print("Jarvis: só consigo guardar a última resposta de modelo ou de documentos.")
            continue

        try:
            with spinner:
                resultado = jarvis.responder(entrada)
            if resultado is None:  # spinner já parou: pode pedir input com segurança
                resultado = jarvis.aprender_ou_eliza(entrada)
        except KeyboardInterrupt:
            print("\n[resposta interrompida]")
            continue
        resposta, fonte = resultado
        print(f"Jarvis [{fonte}]: {resposta}")
        voz.falar(resposta)  # [VOZ] fala em segundo plano; você já pode digitar

    voz.encerrar()  # [VOZ]


if __name__ == "__main__":
    main()