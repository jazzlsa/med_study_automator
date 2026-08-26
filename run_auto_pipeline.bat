@echo off
REM Wrapper usado pela Tarefa Agendada do Windows para rodar o pipeline automatico
REM todo dia. Usa %~dp0 para garantir que o diretorio de trabalho seja sempre a
REM raiz do projeto (necessario: config.yaml, .env e os logs usam caminhos
REM relativos), independente de qual "Start in" o Agendador de Tarefas configurar.
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
"venv\Scripts\python.exe" auto_pipeline.py >> logs\auto_pipeline_task_output.log 2>&1
