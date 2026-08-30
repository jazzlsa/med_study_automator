Set WshShell = CreateObject("WScript.Shell")
WshShell.Run "cmd /c cd /d C:\Users\jessi\med_study_automator && venv\Scripts\python.exe sync_cloud_flashcards_to_anki.py >> logs\anki_sync_task.log 2>&1", 0, False
