@echo off
REM Mantem a sessao do NotebookLM sempre viva: roda "notebooklm auth refresh"
REM (recomendado pela propria CLI a cada 15-20min pra perfil ocioso nao expirar)
REM e, se a sessao mudar, ja sobe o storage_state.json novo pro Secret Manager -
REM assim o Cloud Run sempre pega uma sessao valida, sem precisar de login manual
REM toda hora. Agendado via Tarefa Agendada do Windows (RefreshNotebookLMSession).
cd /d "%~dp0"

set GCLOUD=%LOCALAPPDATA%\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd

".\venv\Scripts\notebooklm.exe" auth refresh --verify >> logs\notebooklm_refresh.log 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo %DATE% %TIME% - refresh falhou, sessao pode precisar de "notebooklm login" manual >> logs\notebooklm_refresh.log
    exit /b 1
)

REM Só storage_state.json muda nesse refresh (o proprio "auth refresh" só
REM rotaciona esse arquivo, nao mexe no master_token.json).
"%GCLOUD%" secrets versions add notebooklm-storage-state --data-file="%USERPROFILE%\.notebooklm\profiles\default\storage_state.json" --quiet >> logs\notebooklm_refresh.log 2>&1

echo %DATE% %TIME% - refresh OK, secrets atualizados >> logs\notebooklm_refresh.log
