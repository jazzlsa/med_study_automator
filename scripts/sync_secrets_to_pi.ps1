# Script para sincronizar credenciais e arquivos de autenticação do Windows para o Raspberry Pi
$ErrorActionPreference = "Stop"

$piUser = "ueti"
$piHost = "192.168.15.31"
$sshKey = "$env:USERPROFILE\.ssh\medstudy_pi_ed25519"
$projectDir = "C:\Users\jessi\med_study_automator"

Write-Host "Iniciando transferência de credenciais para o Raspberry Pi..." -ForegroundColor Cyan

# 1. Copiar arquivo .env e credenciais do projeto
Write-Host "1/3 Copiando .env e config/*.json..." -ForegroundColor Yellow
scp -i "$sshKey" -o StrictHostKeyChecking=no "$projectDir\.env" "${piUser}@${piHost}:/home/ueti/med_study_automator/.env"

if (Test-Path "$projectDir\config\credentials.json") {
    scp -i "$sshKey" -o StrictHostKeyChecking=no "$projectDir\config\credentials.json" "${piUser}@${piHost}:/home/ueti/med_study_automator/config/credentials.json"
}

if (Test-Path "$projectDir\config\oauth_client_secret.json") {
    scp -i "$sshKey" -o StrictHostKeyChecking=no "$projectDir\config\oauth_client_secret.json" "${piUser}@${piHost}:/home/ueti/med_study_automator/config/oauth_client_secret.json"
}

if (Test-Path "$projectDir\config\drive_oauth_secrets.json") {
    scp -i "$sshKey" -o StrictHostKeyChecking=no "$projectDir\config\drive_oauth_secrets.json" "${piUser}@${piHost}:/home/ueti/med_study_automator/config/drive_oauth_secrets.json"
}

# 2. Copiar tokens de autenticação do NotebookLM
Write-Host "2/4 Copiando sessao do NotebookLM..." -ForegroundColor Yellow
$nlmDir = "$env:USERPROFILE\.notebooklm"
if (Test-Path "$nlmDir\config.json") {
    scp -i "$sshKey" -o StrictHostKeyChecking=no "$nlmDir\config.json" "${piUser}@${piHost}:~/.notebooklm/config.json"
}

$nlmProfileDir = "$env:USERPROFILE\.notebooklm\profiles\default"
if (Test-Path "$nlmProfileDir") {
    Get-ChildItem -Path "$nlmProfileDir" -File | ForEach-Object {
        if ($_.Name -notlike "*.lock") {
            scp -i "$sshKey" -o StrictHostKeyChecking=no $_.FullName "${piUser}@${piHost}:~/.notebooklm/profiles/default/$($_.Name)"
        }
    }
}

# 3. Copiar configuração e sessão do Claude Code
Write-Host "3/4 Copiando configuracao e sessao do Claude Code..." -ForegroundColor Yellow
if (Test-Path "$env:USERPROFILE\.claude.json") {
    scp -i "$sshKey" -o StrictHostKeyChecking=no "$env:USERPROFILE\.claude.json" "${piUser}@${piHost}:~/.claude.json"
}
if (Test-Path "$env:USERPROFILE\.claude") {
    ssh -i "$sshKey" -o StrictHostKeyChecking=no "${piUser}@${piHost}" "mkdir -p ~/.claude"
    if (Test-Path "$env:USERPROFILE\.claude\settings.json") {
        scp -i "$sshKey" -o StrictHostKeyChecking=no "$env:USERPROFILE\.claude\settings.json" "${piUser}@${piHost}:~/.claude/settings.json"
    }
}

Write-Host "4/4 Concluído com sucesso! Todas as credenciais foram enviadas ao Raspberry Pi." -ForegroundColor Green
