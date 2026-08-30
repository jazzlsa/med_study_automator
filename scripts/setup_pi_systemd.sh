#!/bin/bash
# Script de configuração dos serviços systemd para o MedStudy Automator no Raspberry Pi
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_USER="ueti"

echo "=== Configurando serviços systemd do MedStudy Automator ==="
echo "Diretório do projeto: ${SCRIPT_DIR}"
echo "Usuário: ${SERVICE_USER}"

# 1. Serviço e Timer do Pipeline Principal (Processamento de Aulas)
sudo tee /etc/systemd/system/medstudy-pipeline.service > /dev/null <<EOF
[Unit]
Description=MedStudy Automator Pipeline Service
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=${SERVICE_USER}
Group=${SERVICE_USER}
WorkingDirectory=${SCRIPT_DIR}
Environment=HOME=/home/${SERVICE_USER}
Environment=PYTHONUNBUFFERED=1
Environment=PATH=${SCRIPT_DIR}/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin
ExecStart=${SCRIPT_DIR}/venv/bin/python ${SCRIPT_DIR}/auto_pipeline.py
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

sudo tee /etc/systemd/system/medstudy-pipeline.timer > /dev/null <<EOF
[Unit]
Description=Executa o MedStudy Automator Pipeline periodicamente
After=network-online.target

[Timer]
OnBootSec=2min
OnCalendar=hourly
Persistent=true

[Install]
WantedBy=timers.target
EOF

# 2. Serviço e Timer do Refresh de Sessão do NotebookLM (Keep-Alive)
sudo tee /etc/systemd/system/medstudy-refresh.service > /dev/null <<EOF
[Unit]
Description=MedStudy Automator NotebookLM Session Refresh Keepalive
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=${SERVICE_USER}
Group=${SERVICE_USER}
WorkingDirectory=${SCRIPT_DIR}
Environment=HOME=/home/${SERVICE_USER}
Environment=PATH=${SCRIPT_DIR}/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin
ExecStart=${SCRIPT_DIR}/venv/bin/notebooklm auth refresh --verify
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

sudo tee /etc/systemd/system/medstudy-refresh.timer > /dev/null <<EOF
[Unit]
Description=Renova o token do NotebookLM a cada 20 minutos
After=network-online.target

[Timer]
OnBootSec=1min
OnUnitActiveSec=20min
Persistent=true

[Install]
WantedBy=timers.target
EOF

echo "Recarregando daemon do systemd..."
sudo systemctl daemon-reload

echo "Ativando timers..."
sudo systemctl enable --now medstudy-pipeline.timer
sudo systemctl enable --now medstudy-refresh.timer

echo "=== Configuração concluída com sucesso! ==="
echo "Status dos timers:"
sudo systemctl list-timers | grep -E "medstudy"
