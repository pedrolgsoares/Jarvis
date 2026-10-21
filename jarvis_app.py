"""Interface gráfica do Jarvis: chat + rede neural animada que reage à fala.

    pip install streamlit edge-tts python-dotenv
    streamlit run jarvis_app.py

Coloque na mesma pasta de jarvis_hibrido.py, jarvis_voz.py e jarvis_rede.html.
O cérebro é o mesmo do terminal (base -> docs -> Ollama -> API). Só a "casca" muda:
aqui o áudio é tocado no navegador, e a rede neural pulsa com o volume real da voz.
A voz vem da ElevenLabs se ELEVENLABS_API_KEY/ELEVENLABS_VOICE_ID estiverem no .env;
senão, da voz neural gratuita do Edge.
"""

import base64
import json
import uuid
from datetime import datetime
from pathlib import Path

import streamlit as st

from jarvis_hibrido import Jarvis
from jarvis_voz import VozIndisponivel, gerar_mp3, remover_tags, status_elevenlabs

HTML_REDE = Path(__file__).with_name("jarvis_hud.html").read_text(encoding="utf-8")
ALTURA_REDE = 440
FONTES_GUARDAVEIS = ("llm-local", "llm-api", "docs")

st.set_page_config(page_title="Jarvis", page_icon="🧠", layout="centered")


@st.cache_resource  # um único Jarvis vivo entre os reruns do Streamlit
def criar_jarvis() -> Jarvis:
    return Jarvis()


def desenhar_rede(area, estado: str, audio: bytes | None = None) -> None:
    """Redesenha o componente da rede. estado: ocioso | pensando | falando."""
    config = {
        "estado": estado,
        "audio": base64.b64encode(audio).decode("ascii") if audio else None,
        "id": uuid.uuid4().hex,  # garante que o navegador recrie o componente
    }
    with area:
        st.iframe(HTML_REDE.replace("__CONFIG__", json.dumps(config)), height=ALTURA_REDE)


def responder(jarvis: Jarvis, entrada: str) -> tuple[str, str]:
    """Roda a cadeia do Jarvis mostrando cada etapa num st.status."""
    with st.status("Pensando…", expanded=False) as status:
        jarvis.ao_progresso = lambda mensagem: status.update(label=mensagem.capitalize())
        jarvis.ao_avisar = lambda mensagem: status.write(mensagem.strip())
        resultado = jarvis.responder(entrada)
        if resultado is None:  # no terminal ele pediria para ensinar; aqui usa a ELIZA
            resposta = jarvis.eliza.responder(entrada)
            jarvis.registrar(entrada, resposta, "eliza")
            resultado = (resposta, "eliza")
        status.update(label=f"Respondido via {resultado[1]}", state="complete")
    return resultado


# --------------------------------------------------------------------------
# Página
# --------------------------------------------------------------------------
jarvis = criar_jarvis()
st.session_state.setdefault("mensagens", [])

with st.sidebar:
    st.header("Jarvis")
    jarvis.modo = st.radio(
        "Cérebro", ["auto", "local", "api"], index=["auto", "local", "api"].index(jarvis.modo),
        help="auto: tenta o Ollama e, se falhar, a API.",
    )
    st.toggle("Falar as respostas", value=True, key="voz")

    if st.button("Guardar última resposta na base"):
        if jarvis.ultima and jarvis.ultima[2] in FONTES_GUARDAVEIS:
            jarvis.guardar()
            st.toast("Guardado. Da próxima vez respondo direto da base.")
        else:
            st.toast("Só dá para guardar respostas do modelo ou dos documentos.")
    if st.button("Reindexar anotações"):
        with st.spinner("Lendo a pasta docs/…"):
            jarvis.docs.carregar()
        st.toast(f"{len(jarvis.docs.trechos)} trechos indexados.")
    if st.button("Limpar conversa"):
        jarvis.historico.clear()
        st.session_state.mensagens = []
    if st.button("Verificar status"):  # sob demanda: checar o Ollama pode levar 2 s
        st.code(jarvis.status(), language=None)

    st.divider()
    st.subheader("ElevenLabs")
    if st.button("Atualizar créditos"):  # sob demanda: faz uma chamada HTTP
        st.session_state.eleven = status_elevenlabs()
    eleven = st.session_state.setdefault("eleven", status_elevenlabs())
    if not eleven["ok"]:
        st.error(eleven["mensagem"])
    elif "limite" not in eleven:
        st.warning(eleven["mensagem"])
    else:
        st.success(f"{eleven['mensagem']} · plano {eleven['plano']}")
        st.progress(min(eleven["usados"] / eleven["limite"], 1.0) if eleven["limite"] else 1.0)
        st.caption(f"{eleven['restantes']:,} de {eleven['limite']:,} créditos restantes".replace(",", "."))
        if eleven["renovacao"]:
            st.caption("Renova em " + datetime.fromtimestamp(eleven["renovacao"]).strftime("%d/%m/%Y"))

area_rede = st.empty()
desenhar_rede(area_rede, "ocioso")

if not st.session_state.mensagens:
    st.caption("Pergunte algo. Eu procuro na base, nas suas anotações e no modelo, nessa ordem.")

for mensagem in st.session_state.mensagens:
    with st.chat_message(mensagem["papel"]):
        st.markdown(remover_tags(mensagem["texto"]))  # tags de emoção só servem para a voz
        if mensagem.get("fonte"):
            st.caption(f"fonte: {mensagem['fonte']}")

entrada = st.chat_input("Pergunte algo ao Jarvis")
if entrada:
    st.session_state.mensagens.append({"papel": "user", "texto": entrada})
    with st.chat_message("user"):
        st.markdown(entrada)

    desenhar_rede(area_rede, "pensando")
    with st.chat_message("assistant"):
        resposta, fonte = responder(jarvis, entrada)
        st.markdown(remover_tags(resposta))
        st.caption(f"fonte: {fonte}")
    st.session_state.mensagens.append({"papel": "assistant", "texto": resposta, "fonte": fonte})

    audio = None
    if st.session_state.voz:
        try:
            audio = gerar_mp3(resposta)
        except VozIndisponivel as erro:
            st.toast(f"Sem voz agora: {erro}")
    desenhar_rede(area_rede, "falando" if audio else "ocioso", audio)