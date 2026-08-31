#!/bin/bash
# MedStudy Automator - Atualizar o codigo do Raspberry Pi a partir do GitHub
# Uso: update
set -e
PROJ="$HOME/med_study_automator"
VENV="$PROJ/venv"

echo "============================================="
echo "  MEDSTUDY AUTOMATOR - ATUALIZACAO"
echo "============================================="

# 1. Atualizar o codigo do GitHub
echo "[1/4] Buscando atualizacoes no GitHub..."
cd "$PROJ"
git fetch origin
LOCAL=$(git rev-parse @)
REMOTE=$(git rev-parse @{u})
if [ "$LOCAL" = "$REMOTE" ]; then
    echo "  Nenhuma atualizacao disponivel (ja esta na versao mais recente)."
else
    git reset --hard origin/main
    echo "  Codigo atualizado para a versao mais recente."
fi

# 2. Reinstalar dependencias (caso algo tenha mudado no requirements.txt)
echo "[2/4] Verificando/instalando dependencias Python..."
"$VENV/bin/pip" install --quiet --upgrade -r "$PROJ/requirements.txt" 2>/dev/null || echo "  (aviso: nao foi possivel atualizar dependencias)"

# 3. Reiniciar os servicos do pipeline
echo "[3/4] Reiniciando servicos do pipeline..."
sudo systemctl restart medstudy-pipeline.service medstudy-refresh.service || echo "  (aviso: servicos nao reiniciados)"

# 4. Confirmar status
echo "[4/4] Confirmando..."
sudo systemctl daemon-reload
systemctl list-timers 2>/dev/null | grep -E 'medstudy|UNIT' || echo "  (timers nao encontrados)"
echo "============================================="
echo "  ATUALIZACAO CONCLUIDA"
echo "  Rode 'status' para conferir o estado final."
echo "============================================="
