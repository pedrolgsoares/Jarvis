# Jarvis

Projeto de estudos: um assistente pessoal em Python que começou como um chatbot de regras e evoluiu, passo a passo, até uma assistente com **cérebro local (Ollama)**, **busca nas suas anotações (RAG)**, **web** (clima, notícias), **voz** (ElevenLabs, Edge, Windows) e uma **interface com HUD** no navegador.

Tudo funciona em cadeia com fallback: se uma fonte falha, tenta a próxima, e o Jarvis nunca fica mudo.

## Como a resposta é decidida

```
pergunta
  1. BASE      frases que você ensinou (conhecimento.json, busca TF-IDF)
  2. CLIMA     tempo/temperatura/chuva -> Open-Meteo (dado ao vivo)
  3. DOCS      trechos das suas anotações (pasta docs/, .txt e .md) viram contexto
  4. WEB       notícias, cotações, "pesquise..." -> DuckDuckGo, e o modelo resume
  5. LLM LOCAL Ollama na sua máquina (sem chave, sem internet)
  6. LLM API   Claude, só se o local falhar E existir ANTHROPIC_API_KEY
  7. WEB       plano B: sem modelo, lê o começo dos resultados da busca
  8. APRENDER  pede para você ensinar (só no terminal)
  9. ELIZA     último recurso, para nunca ficar mudo
```

A fala segue outra cadeia: **ElevenLabs → Edge (neural, grátis) → voz do Windows (offline)**.

## Estrutura

| Arquivo | O que é |
|---|---|
| `jarvis_classico.py` | Versão inicial: aprende frases com você (stdlib pura). |
| `jarvis_tfidf.py` | Mesma ideia, mas a busca compara palavras (TF-IDF). |
| `jarvis_eliza.py` | Estilo ELIZA (1966): regras e regex. |
| `jarvis_hibrido.py` | O Jarvis completo: a cadeia acima, no terminal. |
| `jarvis_web.py` | Clima (Open-Meteo) e busca (DuckDuckGo). |
| `jarvis_voz.py` | Texto para fala, com motores trocáveis e fallback. |
| `jarvis_app.py` | Interface Streamlit (chat + HUD + status da ElevenLabs). |
| `jarvis_hud.html` | HUD animado, que reage ao volume real da voz. |
| `jarvis_rede.html` | HUD antigo (grafo de camadas), mantido de reserva. |
| `docs/` | Suas anotações (`.txt` e `.md`), base do RAG. |
| `conhecimento.json` | O que você ensinou ao Jarvis. |
| `cache_voz/` | MP3s já gerados pela ElevenLabs (não paga duas vezes pela mesma frase). |
| `.env` | Chaves e configurações (**não vá para o git**). |

## 1. Instalação

Requer Python 3.10 ou superior (testado com 3.13) no Windows.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1

pip install -r requirements.txt
```

O `requirements.txt` lista só o que o Jarvis usa, com as versões testadas. O arquivo `requirements-ambiente-antigo.txt` é a lista completa do ambiente de antes (inclui PyTorch, Coqui TTS e outros pacotes de experimentos); pode apagá-lo quando quiser.

## 2. LLM local com Ollama

O Ollama roda o modelo no seu PC: sem chave, sem custo e sem enviar nada para fora.

1. Instale o Ollama em <https://ollama.com/download>.
2. Baixe um modelo (o padrão do projeto é o `llama3.2`, com cerca de 2 GB):

   ```powershell
   ollama pull llama3.2
   ollama list          # confirma que o modelo apareceu
   ```

3. O Ollama sobe um servidor em `http://localhost:11434`. Se ele não estiver rodando, abra o app do Ollama ou use `ollama serve`.
4. Para usar outro modelo ou outro endereço, defina no `.env`:

   ```
   OLLAMA_MODEL=llama3.2
   OLLAMA_URL=http://localhost:11434
   ```

Teste rápido: `ollama run llama3.2 "Diga olá em português"`.

## 3. Suas anotações (RAG)

Coloque arquivos `.txt` ou `.md` na pasta `docs/`. O Jarvis os divide em trechos, indexa com TF-IDF e entrega os mais relevantes ao modelo como contexto.

- Para reindexar depois de mudar os arquivos: `/recarregar` no terminal, ou o botão **Reindexar anotações** no Streamlit.
- **Privacidade:** os trechos das suas anotações **não** são enviados à API da Anthropic. Para permitir, use `JARVIS_DOCS_NA_API=1`. Com o Ollama, eles ficam no seu PC.

## 4. API da Anthropic (opcional)

É o plano B quando o Ollama está fora do ar. No `.env`:

```
ANTHROPIC_API_KEY=sua-chave
ANTHROPIC_MODEL=claude-sonnet-5-5
```

Sem a chave, essa etapa simplesmente fica desligada.

## 5. Web: clima e busca

Funciona sem chave nenhuma.

- **Clima:** perguntas como "qual a temperatura em Guarabira?" ou "vai chover amanhã em Curitiba?" vão direto à Open-Meteo, antes do modelo.
- **Busca:** perguntas com "notícias", "cotação", "dólar", "placar" ou "pesquise..." vão ao DuckDuckGo, e o modelo resume. Sem modelo, o Jarvis lê o começo dos resultados.

Configurações opcionais no `.env`:

```
JARVIS_CIDADE=Guarabira      # usada quando a pergunta de clima não cita cidade
JARVIS_PAIS=BR               # ajuda a achar a cidade certa
JARVIS_WEB=0                 # desliga toda a web
```

**Privacidade:** o nome da cidade vai para a Open-Meteo e a sua pergunta vai para o DuckDuckGo.

## 6. Voz

O Jarvis fala as respostas numa thread própria, com três motores em cadeia:

| Motor | Qualidade | Custo | Precisa de |
|---|---|---|---|
| **ElevenLabs** | a mais humana, entende tags como `[laughs]` | gasta créditos | chave e ID da voz |
| **Edge** (`pt-BR-AntonioNeural`) | boa, neural | grátis | internet |
| **Sistema** (pyttsx3) | robótica | grátis | nada (offline) |

Por padrão a ordem é essa. Para forçar um motor: `JARVIS_MOTOR_VOZ=elevenlabs`, `edge` ou `sistema`.

### 6.1 ElevenLabs

1. Crie uma conta em <https://elevenlabs.io> e gere uma chave em **Developers > API Keys**.
2. Na chave, ative a permissão **Usuário > Acesso** (`user_read`). Sem ela a voz funciona, mas o painel não consegue mostrar os créditos.
3. Escolha uma voz e copie o ID.

   **Atenção ao plano gratuito:** ele **não** permite vozes da *Voice Library* pela API (erro `HTTP 402: Free users cannot use library voices via the API`). Use vozes **premade**, que aparecem em **Vozes > Minhas Vozes**. Em cada voz, o menu (⋮) tem *Copiar ID da voz*.

4. Coloque no `.env`:

   ```
   ELEVENLABS_API_KEY=sua-chave
   ELEVENLABS_VOICE_ID=id-da-voz
   ```

5. Teste:

   ```powershell
   python jarvis_voz.py
   ```

   O status deve listar `elevenlabs` na cadeia e **não** mostrar a linha `falharam na última fala`. Se aparecer, a mensagem diz o motivo (créditos, voz bloqueada, chave sem permissão...).

**Sotaque.** As vozes premade são em inglês, e o modelo `eleven_v3` fala português com elas. Vozes britânicas (como a Alice) tendem a sair com português de Portugal. Vozes americanas (como a Jessica) soaram mais brasileiras nos testes do projeto. Vozes brasileiras de verdade só pela Voice Library, com plano pago.

**Créditos.** Cada caractere enviado gasta 1 crédito. O Jarvis guarda cada áudio em `cache_voz/`: a mesma frase com a mesma voz nunca é cobrada duas vezes. Trocar a voz, o modelo ou a estabilidade gera novas frases e novo gasto.

Variáveis opcionais:

```
ELEVENLABS_MODEL=eleven_v3        # o que entende as tags de emoção
ELEVENLABS_ESTABILIDADE=0.5       # 0.0 criativa | 0.5 natural | 1.0 estável
```

### 6.2 Edge e voz do sistema

Não precisam de chave. Ajustes opcionais:

```
JARVIS_VOZ_EDGE=pt-BR-AntonioNeural     # feminina: pt-BR-FranciscaNeural
JARVIS_RITMO_EDGE=-5%
JARVIS_TOM_EDGE=+0Hz
JARVIS_VOZ=maria                        # parte do nome da voz do Windows
JARVIS_VELOCIDADE=185                   # palavras por minuto (voz do sistema)
```

Para listar as vozes do Edge: `edge-tts --list-voices`.

### 6.3 Pronúncia de siglas

O texto passa por uma limpeza antes de ser falado (markdown, listas, códigos como `A01:2021`, siglas). Para ensinar uma pronúncia, crie `pronuncia.json` ao lado do `jarvis_voz.py`:

```json
{"Kubernetes": "cubernétis", "SIGAWMS": "siga w m s"}
```

## 7. Como rodar

### Terminal

```powershell
python jarvis_hibrido.py
```

Comandos: `/ajuda`, `/status`, `/modo auto|local|api`, `/voz on|off`, `/calar`, `/guardar`, `/recarregar`, `/limpar`, `/sair`.

`/guardar` salva a última resposta do modelo na base: da próxima vez o Jarvis responde direto, sem chamar o modelo.

### Interface gráfica (Streamlit)

```powershell
python -m streamlit run jarvis_app.py
```

Abre em `http://localhost:8501`. Tem o chat, o HUD animado (muda de cor e de ritmo ao ouvir, pensar e falar, e o espectro segue o volume real da voz), a escolha do cérebro e o painel da ElevenLabs.

O painel **ElevenLabs** na barra lateral mostra o status da API e quantos créditos restam. Para os créditos aparecerem, a chave precisa da permissão `user_read` (passo 2 da seção 6.1).

**Depois de mudar o código, reinicie o servidor** (`Ctrl+C` e rode de novo). O `F5` não basta: o Streamlit mantém o Jarvis antigo em memória.

## 8. Problemas comuns

| Sintoma | Causa provável | Solução |
|---|---|---|
| A voz sai do próprio PC (robótica) | A ElevenLabs falhou e caiu no fallback | Rode `python jarvis_voz.py` e leia `falharam na última fala` |
| `HTTP 402 ... library voices` | Voz da biblioteca no plano gratuito | Use uma voz premade (seção 6.1) |
| `HTTP 401 ... user_read` no painel | Chave sem permissão de leitura do usuário | Ative **Usuário > Acesso** na chave |
| Resposta "não sei" para clima ou notícias | Servidor Streamlit com código antigo | Reinicie o Streamlit |
| `ollama: inativo` no `/status` | Servidor do Ollama parado | Abra o app do Ollama ou rode `ollama serve` |
| "diga a cidade" no clima | Pergunta sem cidade e sem `JARVIS_CIDADE` | Cite a cidade ou defina a variável |
| `ConnectionResetError WinError 10054` no terminal do Streamlit | Ruído do asyncio no Windows ao fechar a aba | Pode ignorar |

## 9. Segurança

- O `.env` guarda chaves: **não o publique** nem o versione. Se uma chave vazar, revogue e gere outra.
- Dados que saem do seu PC: o texto falado vai à ElevenLabs e ao Edge; a cidade vai à Open-Meteo; buscas vão ao DuckDuckGo; perguntas vão à Anthropic apenas se o Ollama falhar e houver chave.
- As suas anotações em `docs/` só vão à API da Anthropic se você definir `JARVIS_DOCS_NA_API=1`.

## 10. O que já foi feito

**Cérebro**
- [x] Chatbot que aprende frases e grava em `conhecimento.json` (`jarvis_classico.py`)
- [x] Busca por palavras com TF-IDF em vez de letras (`jarvis_tfidf.py`)
- [x] Versão por regras e regex, estilo ELIZA (`jarvis_eliza.py`)
- [x] Cadeia híbrida com fallback automático entre as fontes (`jarvis_hibrido.py`)
- [x] LLM local com Ollama (`llama3.2`)
- [x] Fallback para a API da Anthropic, só quando há chave
- [x] RAG sobre as anotações em `docs/`, sem enviar os trechos à API por padrão
- [x] Memória de conversa (últimas 20 mensagens) e `/guardar` para transformar respostas em conhecimento

**Web**
- [x] Clima ao vivo pela Open-Meteo, com cidade na pergunta ou padrão em `JARVIS_CIDADE`
- [x] Previsão de amanhã e cidades com mais de uma palavra ("Foz do Iguaçu", "Guarabira Paraíba")
- [x] Busca no DuckDuckGo para notícias, cotações e "pesquise...", com resumo pelo modelo
- [x] Plano B sem modelo: lê o começo dos resultados da busca

**Voz**
- [x] Cadeia ElevenLabs → Edge → voz do Windows, com fallback
- [x] Cache em disco dos áudios da ElevenLabs, para não pagar duas vezes pela mesma frase
- [x] Limpeza do texto para fala (markdown, listas, siglas, códigos como `A01:2021`) e `pronuncia.json`
- [x] Tags de emoção (`[laughs]`) só para a ElevenLabs; os outros motores recebem o texto sem elas
- [x] Diagnóstico do erro 402 (voz da biblioteca no plano gratuito) e troca para uma voz premade (Jessica)

**Interface**
- [x] Streamlit com chat, escolha do cérebro e botões de apoio
- [x] HUD animado (`jarvis_hud.html`): orbe, anel orbital e rede neural, que reage ao volume real da voz
- [x] Painel da ElevenLabs na barra lateral, com status da API e créditos
- [x] Migração do `components.html` (descontinuado) para `st.iframe`
- [x] `requirements.txt` enxuto, só com o que o Jarvis usa e versões fixas
- [x] `.gitignore` protegendo `.env`, `.venv/`, `cache_voz/` e caches

**Ainda sem teste completo** (o código existe e compila, mas falta ver funcionando de ponta a ponta):
- [ ] O HUD nos estados "pensando" e "falando", com áudio de verdade no navegador
- [ ] O autoplay do áudio depois da migração para `st.iframe`
- [ ] O painel de créditos, depois de ativar a permissão `user_read` na chave
- [ ] A cadeia completa com o modo `api` (só foi testada com o Ollama)

## 11. O que podemos melhorar

**Prioridade alta**
- [ ] Iniciar o repositório git (`git init`) e conferir com `git status` que o `.env` não aparece
- [ ] Testes automáticos para as partes sem rede: `limpar_para_fala`, `_cidade_da_pergunta`, detecção de clima e de busca
- [ ] Ampliar os gatilhos da busca na web. Hoje "quem ganhou o jogo ontem?" ainda vai para o modelo, que não sabe
- [ ] Persistir o histórico da conversa entre sessões

**Voz e entrada**
- [ ] Entrada por voz (microfone) com transcrição, como o botão do HUD de referência
- [ ] Voz brasileira: uma voz da biblioteca (plano pago) ou voz clonada local com XTTS-v2 ou Chatterbox, que não gasta créditos. Só clone uma voz que seja sua ou autorizada
- [ ] Botão no Streamlit para calar a fala (`/calar` só existe no terminal)
- [ ] Alerta quando os créditos da ElevenLabs estiverem acabando e troca automática para o Edge

**Cérebro**
- [ ] Respostas em streaming, para o Jarvis começar a falar antes de terminar de pensar
- [ ] RAG com embeddings (por exemplo `nomic-embed-text` no Ollama) no lugar do TF-IDF, que só compara palavras iguais
- [ ] Ferramentas (hora, data, calculadora, arquivos locais) escolhidas pelo modelo
- [ ] Indicar a fonte e o link nas respostas vindas da web
- [ ] Escolher o modelo do Ollama pela interface

**Interface**
- [ ] Mostrar na tela o motor de voz em uso e o último erro da voz (hoje só aparece no terminal ou no `python jarvis_voz.py`)
- [ ] Tema claro e ajuste do HUD para telas pequenas
- [ ] Empacotar com um atalho que sobe o Ollama e o Streamlit juntos
