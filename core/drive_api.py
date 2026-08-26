"""Cliente da Drive API real (google-api-python-client), usado só quando
STORAGE_BACKEND=cloud (core/drive_sync.py escolhe a implementação certa).

Duas identidades diferentes, cada uma pro que sabe fazer:

- **Leitura** (listar/baixar aulas): service account
  (settings.secrets.GOOGLE_CREDENTIALS_PATH) - a única coisa que o usuário
  precisa fazer é compartilhar a pasta "MedStudy_Aulas" (leitura) com o e-mail
  dela.
- **Escrita** (subir/criar o .apkg de flashcards): OAuth com a conta Google
  PESSOAL do usuário (GOOGLE_DRIVE_OAUTH_CLIENT_ID/SECRET/REFRESH_TOKEN) - uma
  service account não tem cota de armazenamento própria no Drive ("Service
  Accounts do not have storage quota", erro real visto em produção), então
  upload por ela SEMPRE falha em Meu Drive normal (só funciona em Shared
  Drive, recurso do Workspace pago). Autenticando como o próprio usuário, o
  arquivo conta na cota pessoal dele (que sobra), sem depender de Workspace.
  Sem essas 3 env vars configuradas, cai de volta pra service account no
  upload também (funciona só se a pasta de destino for uma Shared Drive).

Localiza as pastas raiz PELO NOME (busca `files().list` por
name = '<pasta>' and mimeType = pasta), não por ID fixo - assim o usuário não
precisa caçar IDs de pasta manualmente, só compartilhar. Dá pra sobrepor via
GOOGLE_DRIVE_LESSONS_FOLDER_ID / GOOGLE_DRIVE_FLASHCARDS_FOLDER_ID no .env se a
busca por nome for ambígua (duas pastas com o mesmo nome compartilhadas).
"""
import io
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional

from utils.logger import logger
from config.settings import settings

try:
    from google.oauth2 import service_account, credentials as oauth_credentials
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload
    DRIVE_API_AVAILABLE = True
except ImportError:
    DRIVE_API_AVAILABLE = False

SCOPES = ["https://www.googleapis.com/auth/drive"]
# Precisa do escopo "drive" completo (não dá pra usar o mais restrito
# "drive.file"): a pasta MedStudy_Flashcards já existe de antes, criada por
# fora deste app - sob "drive.file" o app só enxergaria arquivos/pastas que
# ELE MESMO criou (ou abertos via seletor de arquivo), então não conseguiria
# nem localizar a pasta existente, só criar coisas novas soltas.
OAUTH_UPLOAD_SCOPES = ["https://www.googleapis.com/auth/drive"]
OAUTH_TOKEN_URI = "https://oauth2.googleapis.com/token"
FOLDER_MIME = "application/vnd.google-apps.folder"


class DriveApiClient:
    """Fina camada sobre a Drive API v3: localizar pastas por nome, listar
    conteúdo, baixar e subir arquivo. Nada aqui sabe o que é uma "aula" - isso
    fica em core/drive_sync.py (DriveApiScanner), que usa este cliente."""

    def __init__(self):
        self._service = None
        self._oauth_service = None

    @property
    def enabled(self) -> bool:
        return bool(
            DRIVE_API_AVAILABLE
            and settings.secrets.GOOGLE_CREDENTIALS_PATH
            and Path(settings.secrets.GOOGLE_CREDENTIALS_PATH).exists()
        )

    @property
    def oauth_upload_enabled(self) -> bool:
        return bool(
            DRIVE_API_AVAILABLE
            and settings.secrets.GOOGLE_DRIVE_OAUTH_CLIENT_ID
            and settings.secrets.GOOGLE_DRIVE_OAUTH_CLIENT_SECRET
            and settings.secrets.GOOGLE_DRIVE_OAUTH_REFRESH_TOKEN
        )

    def _get_service(self):
        if self._service is not None:
            return self._service
        creds = service_account.Credentials.from_service_account_file(
            str(settings.secrets.GOOGLE_CREDENTIALS_PATH), scopes=SCOPES
        )
        self._service = build("drive", "v3", credentials=creds, cache_discovery=False)
        return self._service

    def _get_upload_service(self):
        """Serviço usado especificamente pra escrita (upload/criar pasta) - OAuth
        da conta pessoal quando configurado (evita o erro de cota de service
        account), senão cai de volta pra service account (só funciona se o
        destino for uma Shared Drive)."""
        if not self.oauth_upload_enabled:
            if self._oauth_service is None:  # loga o aviso uma única vez
                logger.warning(
                    "GOOGLE_DRIVE_OAUTH_* não configurado - upload vai usar a service account, "
                    "que só funciona se a pasta de destino for uma Shared Drive (não é o caso "
                    "de 'Meu Drive' pessoal - vai falhar com 'storageQuotaExceeded')."
                )
                self._oauth_service = False  # sentinela: já avisou
            return self._get_service()

        if self._oauth_service and self._oauth_service is not False:
            return self._oauth_service

        creds = oauth_credentials.Credentials(
            token=None,
            refresh_token=settings.secrets.GOOGLE_DRIVE_OAUTH_REFRESH_TOKEN,
            token_uri=OAUTH_TOKEN_URI,
            client_id=settings.secrets.GOOGLE_DRIVE_OAUTH_CLIENT_ID,
            client_secret=settings.secrets.GOOGLE_DRIVE_OAUTH_CLIENT_SECRET,
            scopes=OAUTH_UPLOAD_SCOPES,
        )
        self._oauth_service = build("drive", "v3", credentials=creds, cache_discovery=False)
        return self._oauth_service

    def find_root_folder(self, name: str, override_id: Optional[str] = None) -> Optional[str]:
        """Acha o ID de uma pasta raiz pelo nome (compartilhada com a service
        account). `override_id`, se vier preenchido (do .env), é usado direto sem
        buscar - resolve o caso raro de nome ambíguo."""
        if override_id:
            return override_id
        service = self._get_service()
        query = f"name = '{name}' and mimeType = '{FOLDER_MIME}' and trashed = false"
        resp = service.files().list(
            q=query, fields="files(id, name)", spaces="drive",
            includeItemsFromAllDrives=True, supportsAllDrives=True,
        ).execute()
        files = resp.get("files", [])
        if not files:
            logger.warning(f"Pasta '{name}' não encontrada no Drive da service account (foi compartilhada?).")
            return None
        if len(files) > 1:
            logger.warning(
                f"Mais de uma pasta chamada '{name}' compartilhada com a service account; "
                f"usando a primeira ({files[0]['id']}). Configure um ID fixo no .env se isso for um problema."
            )
        return files[0]["id"]

    def list_children(self, folder_id: str) -> List[Dict[str, Any]]:
        """Lista arquivos/subpastas diretos de `folder_id` (não recursivo).

        Normaliza o "name" pra Unicode NFC (composto: "ç" = 1 caractere) - a Drive
        API às vezes devolve nomes em NFD (decomposto: "c" + acento separado como
        caractere combinante), o que já causou bug real em produção: qualquer
        biblioteca que tente tratar esse texto como ASCII em algum ponto da stack
        (o Gemini SDK, por exemplo) quebra com 'ascii codec can't encode
        character \\u0327' - o acento sozinho não é ASCII em nenhuma das duas
        formas, mas só aparece como caractere separado na forma NFD."""
        service = self._get_service()
        files: List[Dict[str, Any]] = []
        page_token = None
        query = f"'{folder_id}' in parents and trashed = false"
        while True:
            resp = service.files().list(
                q=query, fields="nextPageToken, files(id, name, mimeType)",
                spaces="drive", pageToken=page_token,
                includeItemsFromAllDrives=True, supportsAllDrives=True,
            ).execute()
            for f in resp.get("files", []):
                if f.get("name"):
                    f["name"] = unicodedata.normalize("NFC", f["name"])
                files.append(f)
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return files

    def download_file(self, file_id: str, dest_path: Path) -> Path:
        """Baixa o conteúdo de `file_id` para `dest_path` (cria os diretórios pai)."""
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        service = self._get_service()
        request = service.files().get_media(fileId=file_id, supportsAllDrives=True)
        with io.FileIO(dest_path, "wb") as fh:
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
        return dest_path

    def find_or_create_folder(self, parent_id: str, name: str) -> Optional[str]:
        """Acha a subpasta `name` dentro de `parent_id`, criando se não existir.
        Usa o serviço de upload (OAuth, quando configurado) - criar pasta é uma
        escrita, sujeita à mesma questão de propriedade/cota que subir arquivo.

        Retorna None (nunca levanta) em caso de erro - bug real visto em produção:
        uma falha aqui (ex.: "invalid_client: The OAuth client was not found" - o
        client ID/secret do OAuth deixou de ser válido) propagava sem tratamento e
        derrubava o processamento INTEIRO da aula (perdia notebook já criado,
        fontes, tudo) só porque o upload de flashcards - uma etapa não-crítica -
        falhou. Quem chama (publish_flashcards_apkg) já sabe lidar com None."""
        try:
            service = self._get_upload_service()
            query = (
                f"'{parent_id}' in parents and name = '{name}' "
                f"and mimeType = '{FOLDER_MIME}' and trashed = false"
            )
            resp = service.files().list(
                q=query, fields="files(id)", spaces="drive",
                includeItemsFromAllDrives=True, supportsAllDrives=True,
            ).execute()
            files = resp.get("files", [])
            if files:
                return files[0]["id"]
            metadata = {"name": name, "mimeType": FOLDER_MIME, "parents": [parent_id]}
            created = service.files().create(body=metadata, fields="id", supportsAllDrives=True).execute()
            logger.info(f"Subpasta '{name}' criada no Drive (id={created['id']}).")
            return created["id"]
        except Exception as e:
            logger.error(f"Falha ao achar/criar a subpasta '{name}' no Drive (pasta pai {parent_id}): {e}")
            return None

    def upload_file(self, local_path: Path, parent_folder_id: str, filename: Optional[str] = None) -> Dict[str, Any]:
        """Sobe `local_path` para dentro de `parent_folder_id`. Se já existir um
        arquivo com o mesmo nome nessa pasta, SUBSTITUI o conteúdo dele (update)
        em vez de criar um duplicado - mesma semântica de "regerou, sobrescreve"
        que o comportamento local (escrever direto em cima do .apkg antigo).

        Retorna {"success", "file_id", "url", "error"}.
        """
        filename = filename or local_path.name
        try:
            service = self._get_upload_service()
            query = (
                f"'{parent_folder_id}' in parents and name = '{filename}' and trashed = false"
            )
            resp = service.files().list(
                q=query, fields="files(id)", spaces="drive",
                includeItemsFromAllDrives=True, supportsAllDrives=True,
            ).execute()
            existing = resp.get("files", [])
            media = MediaFileUpload(str(local_path), resumable=True)

            if existing:
                file_id = existing[0]["id"]
                service.files().update(fileId=file_id, media_body=media, supportsAllDrives=True).execute()
            else:
                metadata = {"name": filename, "parents": [parent_folder_id]}
                created = service.files().create(
                    body=metadata, media_body=media, fields="id", supportsAllDrives=True
                ).execute()
                file_id = created["id"]

            url = f"https://drive.google.com/file/d/{file_id}/view"
            return {"success": True, "file_id": file_id, "url": url, "error": None}
        except Exception as e:
            logger.error(f"Falha ao subir '{filename}' para o Drive (pasta {parent_folder_id}): {e}")
            return {"success": False, "file_id": None, "url": None, "error": str(e)}


drive_api_client = DriveApiClient()
