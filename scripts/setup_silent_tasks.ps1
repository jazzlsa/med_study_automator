$actionAnki = New-ScheduledTaskAction -Execute "wscript.exe" -Argument '"C:\Users\jessi\med_study_automator\run_anki_sync_hidden.vbs"'
Set-ScheduledTask -TaskName "MedStudyAutomator_AnkiSync" -Action $actionAnki

$actionNLM = New-ScheduledTaskAction -Execute "wscript.exe" -Argument '"C:\Users\jessi\med_study_automator\run_notebooklm_refresh_hidden.vbs"'
Set-ScheduledTask -TaskName "MedStudyAutomator_NotebookLMRefresh" -Action $actionNLM

Write-Host "Tarefas configuradas para modo silencioso com sucesso!" -ForegroundColor Green
