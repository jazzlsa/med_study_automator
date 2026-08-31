#!/bin/bash
# MedStudy Automator - Painel rapido de status do Raspberry Pi
echo "============================================="
echo "  MEDSTUDY AUTOMATOR - STATUS DO RASPBERRY"
echo "============================================="
echo "Data/hora : $(date '+%d/%m/%Y %H:%M')"
echo "Online    : $(uptime -p | sed 's/up //')"
echo "Disco     : $(df -h / | awk 'NR==2{print $3" usado de "$2" ("$5")"}')"
echo "---------------------------------------------"

echo "[1/4] Agendamentos (timers) ativos:"
systemctl list-timers 2>/dev/null | grep -E 'medstudy|NEXT' || echo "  (nenhum timer medstudy encontrado)"
echo "---------------------------------------------"

echo "[2/4] Resultado da ULTIMA execucao do pipeline:"
R=$(systemctl show medstudy-pipeline.service -p Result --value 2>/dev/null)
H=$(systemctl show medstudy-pipeline.service -p ActiveEnterTimestamp --value 2>/dev/null)
echo "  Pipeline: $R  (ultima vez: $H)"
echo "---------------------------------------------"

echo "[3/4] Fim do ultimo LOG do pipeline:"
sudo journalctl -u medstudy-pipeline.service -n 8 --no-pager 2>/dev/null | tail -8 || echo "  (sem log)"
echo "---------------------------------------------"

echo "[4/4] Keep-alive NotebookLM (refresh):"
R2=$(systemctl show medstudy-refresh.service -p Result --value 2>/dev/null)
echo "  Refresh: $R2"
sudo journalctl -u medstudy-refresh.service -n 2 --no-pager 2>/dev/null | grep -E 'ok|error|fail' | tail -2 || echo "  (sem log)"
echo "============================================="
