#!/bin/sh
# Entrypoint do container do Cloud Run Job.
#
# Secrets de arquivo (Secret Manager montado como volume) chegam SOMENTE LEITURA,
# um em cada pasta própria (Cloud Run não deixa dois secrets diferentes dividirem
# a mesma pasta de montagem) - mas a CLI notebooklm-py espera esses arquivos em
# ~/.notebooklm/profiles/default/ e pode querer reescrevê-los (rotação de cookie
# durante o uso). Por isso copiamos pro filesystem normal do container (que É
# gravável, só não persiste entre execuções) antes de rodar o pipeline, em vez de
# apontar direto pro volume montado.
set -e

NOTEBOOKLM_PROFILE_DIR="${HOME:-/root}/.notebooklm/profiles/default"
mkdir -p "$NOTEBOOKLM_PROFILE_DIR"

if [ -f /secrets/notebooklm-master-token/master_token.json ]; then
    cp /secrets/notebooklm-master-token/master_token.json "$NOTEBOOKLM_PROFILE_DIR/master_token.json"
fi
if [ -f /secrets/notebooklm-storage-state/storage_state.json ]; then
    cp /secrets/notebooklm-storage-state/storage_state.json "$NOTEBOOKLM_PROFILE_DIR/storage_state.json"
fi

# PIPELINE_SCRIPT (opcional) troca qual script rodar sem precisar sobrescrever o
# ENTRYPOINT inteiro (que perderia a cópia dos secrets acima) - usado por scripts
# de manutenção pontuais (ex.: regenerate_transcripts.py) via
# `gcloud run jobs execute --update-env-vars PIPELINE_SCRIPT=nome.py`. Default
# continua o pipeline normal, sem precisar tocar em nada pras execuções agendadas.
SCRIPT="${PIPELINE_SCRIPT:-auto_pipeline.py}"
echo "[entrypoint] Iniciando $SCRIPT (STORAGE_BACKEND=${STORAGE_BACKEND:-não definido})..."
exec python "$SCRIPT"
