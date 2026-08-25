"""Cliente da Drive API real (google-api-python-client), usado só quando
STORAGE_BACKEND=cloud (core/drive_sync.py escolhe a implementação certa).

Reaproveita a MESMA credencial de service account do Sheets
(settings.secrets.GOOGLE_CREDENTIALS_PATH) - a única coisa nova que o usuário
precisa fazer é compartilhar as pastas "MedStudy_Aulas" (leitura) e
"MedStudy_Flashcards" (edição) do Drive com o e-mail dessa service account.

Localiza as duas pastas raiz PELO NOME (busca `files().list` por
name = '<pasta>' and mimeType = pasta), não por ID fixo - assim o usuário não
precisa caçar IDs de pasta manualmente, só compartilhar. Dá pra sobrepor via
GOOGLE_DRIVE_LESSONS_FOLDER_ID / GOOGLE_DRIVE_FLASHCARDS_FOLDER_ID no .env se a
busca por nome for ambígua (duas pastas com o mesmo nome compartilhadas).
"""
import io
from pathlib import Path
from typing import Any, Dict, List, Optional

from utils.logger import logger
from config.settings import settings

try:
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload
    DRIVE_API_AVAILABLE = True
except ImportError:
    DRIVE_API_AVAILABLE = False

SCOPES = ["https://www.googleapis.com/auth/drive"]
FOLDER_MIME = "application/vnd.google-apps.folder"

LESSONS_ROOT_NAME = "MedStudy_Aulas"
FLASHCARDS_ROOT_NAME = "MedStudy_Flashcards"


class DriveApiClient:
    """Fina camada sobre a Drive API v3: localizar pastas por nome, listar
    conteúdo, baixar e subir arquivo. Nada aqui sabe o que é uma "aula" - isso
    fica em core/drive_sync.py (DriveApiScanner), que usa este cliente."""

    def __init__(self):
        self._service = None

    @property
    def enabled(self) -> bool:
        return bool(
            DRIVE_API_AVAILABLE
            and settings.secrets.GOOGLE_CREDENTIALS_PATH
            and Path(settings.secrets.GOOGLE_CREDENTIALS_PATH).exists()
        )

    def _get_service(self):
        if self._service is not None:
            return self._service
        creds = service_account.Credentials.from_service_account_file(
            str(settings.secrets.GOOGLE_CREDENTIALS_PATH), scopes=SCOPES
        )
        self._service = build("drive", "v3", credentials=creds, cache_discovery=False)
        return self._service

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
        """Lista arquivos/subpastas diretos de `folder_id` (não recursivo)."""
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
            files.extend(resp.get("files", []))
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

    def find_or_create_folder(self, parent_id: str, name: str) -> str:
        """Acha a subpasta `name` dentro de `parent_id`, criando se não existir."""
        service = self._get_service()
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

    def upload_file(self, local_path: Path, parent_folder_id: str, filename: Optional[str] = None) -> Dict[str, Any]:
        """Sobe `local_path` para dentro de `parent_folder_id`. Se já existir um
        arquivo com o mesmo nome nessa pasta, SUBSTITUI o conteúdo dele (update)
        em vez de criar um duplicado - mesma semântica de "regerou, sobrescreve"
        que o comportamento local (escrever direto em cima do .apkg antigo).

        Retorna {"success", "file_id", "url", "error"}.
        """
        filename = filename or local_path.name
        try:
            service = self._get_service()
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
