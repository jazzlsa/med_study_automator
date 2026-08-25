#!/bin/sh
# Entrypoint do container do Cloud Run Job.
#
# Secrets de arquivo (Secret Manager montado como volume) chegam SOMENTE LEITURA
# em /secrets/notebooklm/*.json - mas a CLI notebooklm-py espera esses arquivos
# em ~/.notebooklm/profiles/default/ e pode querer reescrevê-los (rotação de
# cookie durante o uso). Por isso copiamos pro filesystem normal do container
# (que É gravável, só não persiste entre execuções) antes de rodar o pipeline,
# em vez de apontar direto pro volume montado.
set -e

NOTEBOOKLM_PROFILE_DIR="${HOME:-/root}/.notebooklm/profiles/default"
mkdir -p "$NOTEBOOKLM_PROFILE_DIR"

if [ -f /secrets/notebooklm/master_token.json ]; then
    cp /secrets/notebooklm/master_token.json "$NOTEBOOKLM_PROFILE_DIR/master_token.json"
fi
if [ -f /secrets/notebooklm/storage_state.json ]; then
    cp /secrets/notebooklm/storage_state.json "$NOTEBOOKLM_PROFILE_DIR/storage_state.json"
fi

echo "[entrypoint] Iniciando auto_pipeline.py (STORAGE_BACKEND=${STORAGE_BACKEND:-não definido})..."
exec python auto_pipeline.py
