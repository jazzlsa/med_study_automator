@echo off
echo ========================================================
echo   Inicializando Setup do MedStudyAutomator...
echo ========================================================

REM Cria ambiente virtual se nao existir
if not exist "venv" (
    echo [1/3] Criando ambiente virtual venv...
    python -m venv venv
) else (
    echo [1/3] Ambiente virtual ja existe.
)

REM Ativa ambiente virtual
echo [2/3] Ativando ambiente virtual...
call .\venv\Scripts\activate.bat

REM Instala dependencias
echo [3/3] Instalando/Atualizando dependencias...
pip install --upgrade pip
pip install -r requirements.txt

echo.
echo ========================================================
echo   Setup concluido com sucesso! Padrão 10/10.
echo ========================================================
pause