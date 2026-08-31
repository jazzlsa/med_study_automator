# MedStudy Automator

Automatiza o pipeline de estudo das aulas de medicina: detecta aulas novas na pasta do
Google Drive, transcreve o áudio, gera flashcards com IA e exporta para o Anki — rodando
sozinho numa **Tarefa Agendada do Windows** (dev) ou com **timers systemd no Raspberry Pi**
(produção).

> **Pipeline de IAs:** o **Gemini** faz a transcrição de áudio (e o fallback/vídeos) e o
> **Claude** gera os flashcards a partir da transcrição (texto) + slide (PDF). Ver seção
> *Arquitetura*.

---

## Como rodar

### Local (Windows, dev)

```bat
venv\Scripts\python.exe -m pip install -r requirements.txt
venv\Scripts\python.exe main.py --help        :: CLI (process / stats / list)
venv\Scripts\python.exe app.py                :: UI Streamlit (aba Pipeline + Configurações)
venv\Scripts\python.exe auto_pipeline.py      :: varre TODAS as UCs e processa aulas novas
```

Para testar só algumas UCs sem esperar a varredura completa:

```bat
set PIPELINE_ONLY_UCS=UC16,UC17 && venv\Scripts\python.exe auto_pipeline.py
```

### Raspberry Pi (produção)

A automação roda via timers systemd. Acesse o Pi (Tailscale/Termius) e use os atalhos:

| Atalho | O que faz |
|---|---|
| `./scripts/status.sh` | Status da automação / serviços |
| `./scripts/update.sh` | Puxa (`git pull`) a última versão e reinicia |
| `./scripts/claude.sh` | Abre o Claude Code dentro do projeto no Pi |

O setup inicial do Pi está em `scripts/setup_pi_systemd.sh`. Rode `notebooklm login`
manualmente antes da primeira execução (a sessão do CLI expira e precisa ser reautenticada).

---

## Ponteiros de entrada

| Arquivo | O que é |
|---|---|
| `main.py` | CLI (click + rich): `process`, `stats`, `list` |
| `app.py` | UI Streamlit: aba **📋 Pipeline** e aba **⚙️ Configurações** (edita a config do semestre) |
| `auto_pipeline.py` | Automação não-supervisionada: varre Drive → processa aulas novas → resume |
| `core/orchestrator.py` | Orquestra o pipeline completo de uma aula |
| `core/multimodal_processor.py` | Chama a API multimodal, repara/parseia o JSON retornado |
| `database/db.py` | SQLite local + sync opcional via GCS (só se `GCS_DB_BUCKET` estiver setada) |
| `config/config.yaml` | Configuração editável do semestre (UCs, pastas do Drive, planilha) |

---

## Variáveis de ambiente (`.env`)

Copie de um `.env` existente ou crie um na raiz. As chaves usadas estão em
`config/settings.py` (`EnvSecrets`):

| Variável | Obrigatória? | Uso |
|---|---|---|
| `GEMINI_API_KEY` | sim | Transcrição de áudio / vídeos |
| `ANTHROPIC_API_KEY` | sim | Geração de flashcards (Claude) |
| `ANKI_CONNECT_URL` | não | Padrão `http://localhost:8765` |
| `GOOGLE_SPREADSHEET_ID` / `GOOGLE_CREDENTIALS_PATH` | para sync | Google Sheets / credenciais |
| `GOOGLE_DRIVE_OAUTH_CLIENT_ID` / `_SECRET` / `_REFRESH_TOKEN` | para upload .apkg | OAuth da conta pessoal (gerado por `scripts/setup_drive_oauth.py`) |
| `GCS_DB_BUCKET` | não | Persistência do banco via Cloud Storage (Cloud Run; vazio = local) |
| `NTFY_TOPIC` | não | Alerta por push quando a noite falha (ver *Alerta por push*) |
| `WHATSAPP_API_TOKEN` | — | (não usado ativamente) |

**Credenciais nunca versionadas** (no `.gitignore`): `.env`, `config/credentials.json`,
`config/oauth_client_secret.json`, `config/drive_oauth_secrets.json`.

---

## Arquitetura e notas

- **Fontes de verdade:** a lista de UCs do semestre (`config.yaml > semester.available_ucs`)
  e a planilha do Google Sheets. Tudo que muda a cada semestre fica no `config.yaml`,
  editável pela aba Configurações do `app.py`.
- **Banco:** `database/lessons.db` (config) vs `data/med_automator.db` (real, usado pelo
  `db.py`). A regra atual é: o banco vivo é `data/med_automator.db`.
- **Orçamento do Gemini:** o pipeline respeita o limite diário da API gratuita (ver
  histórico/commits sobre orçamento).
- **Geração de flashcards:** `core/claude_client.py` + regras em `config.yaml > flashcards`
  (mín/máx cards por hora, tags permitidas, extração de imagem dos slides).

---

## Alerta por push

A execução do `auto_pipeline.py` manda um push de notificação (via [ntfy.sh](https://ntfy.sh))
se a env var `NTFY_TOPIC` estiver setada. Dois tipos:

- **Sucesso:** cada aula concluída com sucesso (novo notebook na planilha +
  flashcards gerados) gera um push avisando **qual aula**. Tag `white_check_mark`.
- **Falha:** ao final, se houver falhas, um push com a lista do que falhou. Se
  **todas** as falhas forem na criação do NotebookLM (sintoma de sessão expirada),
  vai com prioridade **urgente**.

Para ativar:
1. Instale o app **ntfy** no celular e assine um tópico (ex.: `meus-medicaid`).
2. Adicione `NTFY_TOPIC=meus-medicaid` no `.env` (Windows) / no ambiente do Pi.
3. Pronto.

Sem `NTFY_TOPIC`, o pipeline se comporta exatamente como antes (nenhuma notificação).

---

## Testes

```bat
venv\Scripts\python.exe -m pytest
```

## Automação no Windows

O pipeline diário roda no Raspberry Pi (timers systemd). No Windows, além do uso manual,
há **Tarefas Agendadas** que ainda usam os scripts na raiz:

| Tarefa | Script |
|---|---|
| `MedStudyAutomator_DailyPipeline` | `run_auto_pipeline.bat` (desabilitada — rodou no Pi) |
| `MedStudyAutomator_AnkiSync` | `run_anki_sync_hidden.vbs` |
| `MedStudyAutomator_NotebookLMRefresh` | `run_notebooklm_refresh_hidden.vbs` |

Cuidado ao mover/renomear esses scripts: as tarefas apontam por **caminho absoluto**
(`C:\Users\jessi\med_study_automator\...`) e quebram se você deslocá-los. O bootstrap
dessas tarefas é feito por `scripts/setup_silent_tasks.ps1`.
