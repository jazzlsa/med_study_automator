"""Bootstrap ÚNICO (roda uma vez só, no Windows) da autorização OAuth usada
pelo pipeline pra LER o Google Classroom das disciplinas (avisos, materiais,
atividades e os anexos deles no Drive) com a conta de aluno.

Mesmo OAuth Client ID "Desktop app" do setup_drive_oauth.py
(config/oauth_client_secret.json). Pré-requisitos no Google Cloud Console,
no mesmo projeto do client:
  - API do Google Classroom ativada;
  - se a tela de consentimento estiver em "Teste", a conta que vai logar
    precisa estar em "Usuários de teste".

Uso: venv\\Scripts\\python.exe scripts\\setup_classroom_oauth.py
Abre o navegador, você loga com a conta que está nas turmas do Classroom e
autoriza. O refresh_token vai pra config/classroom_oauth_secrets.json (não vai
pro Git) e, pra conferir, o script lista as turmas que a conta enxerga.
"""
import json
import os
import sys
from pathlib import Path

# O Google devolve coursework.me.readonly com o nome novo
# (student-submissions.me.readonly); sem isso o oauthlib aborta o login.
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

CLIENT_SECRET_PATH = Path("config/oauth_client_secret.json")
OUT_PATH = Path("config/classroom_oauth_secrets.json")
# Tudo somente leitura: o pipeline nunca escreve no Classroom.
SCOPES = [
    "https://www.googleapis.com/auth/classroom.courses.readonly",
    "https://www.googleapis.com/auth/classroom.announcements.readonly",
    "https://www.googleapis.com/auth/classroom.courseworkmaterials.readonly",
    "https://www.googleapis.com/auth/classroom.coursework.me.readonly",
    "https://www.googleapis.com/auth/classroom.topics.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]


def main() -> int:
    if not CLIENT_SECRET_PATH.exists():
        print(f"ERRO: não achei {CLIENT_SECRET_PATH} (o mesmo do setup_drive_oauth.py).")
        return 1

    with open(CLIENT_SECRET_PATH, "r", encoding="utf-8") as f:
        client_config = json.load(f)
    client_info = client_config.get("installed") or client_config.get("web") or {}

    print("Abrindo o navegador: logue com a conta que está nas turmas do Classroom...")
    flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")

    OUT_PATH.write_text(
        json.dumps(
            {
                "client_id": client_info.get("client_id"),
                "client_secret": client_info.get("client_secret"),
                "refresh_token": creds.refresh_token,
                "scopes": SCOPES,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\nAutorização salva em {OUT_PATH} (não vai pro Git).")

    service = build("classroom", "v1", credentials=creds, cache_discovery=False)
    courses = service.courses().list(courseStates=["ACTIVE"], pageSize=100).execute().get("courses", [])
    print(f"\nTurmas ativas visíveis ({len(courses)}):")
    for c in courses:
        print(f"  - {c['name']}  [id {c['id']}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
