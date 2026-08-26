"""Bootstrap ÚNICO (roda uma vez só, no Windows) da autorização OAuth usada
pelo pipeline pra subir o .apkg de flashcards no Drive como a conta PESSOAL do
usuário, em vez da service account - necessário porque service accounts não
têm cota de armazenamento própria em "Meu Drive" (erro real:
"Service Accounts do not have storage quota").

Pré-requisito: ter criado um OAuth Client ID do tipo "Desktop app" no Google
Cloud Console (APIs e Serviços > Credenciais) e baixado o JSON dele pra
config/oauth_client_secret.json (não commitar esse arquivo - já está no
.gitignore).

Uso: venv\\Scripts\\python.exe scripts\\setup_drive_oauth.py
Abre o navegador, você loga com a SUA conta Google pessoal e autoriza. No
final, imprime os 3 valores pra colar no .env (uso local) e pra subir como
secrets no Secret Manager (uso no Cloud Run) - client_id e client_secret já
vêm do próprio arquivo baixado do Console, só o refresh_token é novo.
"""
import json
import sys
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

CLIENT_SECRET_PATH = Path("config/oauth_client_secret.json")
SCOPES = ["https://www.googleapis.com/auth/drive"]


def main() -> int:
    if not CLIENT_SECRET_PATH.exists():
        print(f"ERRO: não achei {CLIENT_SECRET_PATH}.")
        print(
            "Baixe o JSON do OAuth Client ID (tipo 'Desktop app') no Google Cloud "
            "Console > APIs e Serviços > Credenciais, e salve exatamente nesse caminho."
        )
        return 1

    with open(CLIENT_SECRET_PATH, "r", encoding="utf-8") as f:
        client_config = json.load(f)
    client_info = client_config.get("installed") or client_config.get("web") or {}
    client_id = client_info.get("client_id")
    client_secret = client_info.get("client_secret")

    print("Abrindo o navegador para você autorizar com sua conta Google pessoal...")
    flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
    creds = flow.run_local_server(port=0)

    out_path = Path("config/drive_oauth_secrets.json")
    out_path.write_text(
        json.dumps(
            {
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": creds.refresh_token,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    print("\n" + "=" * 70)
    print(f"Autorização concluída! Valores salvos em {out_path} (não vai pro Git).")
    print("=" * 70)
    print(
        "\nPróximo passo: rode os 3 comandos que o assistente vai te passar pra "
        "subir esses valores como secrets no Secret Manager, direto a partir "
        "desse arquivo (sem precisar copiar/colar nenhum valor à mão)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
