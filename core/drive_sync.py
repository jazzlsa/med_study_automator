import os
from pathlib import Path
from typing import List, Dict, Any, Optional
from utils.logger import logger
from config.settings import settings

SLIDE_EXTS = (".pdf",)
AUDIO_EXTS = (".mp3", ".wav", ".m4a")


class DriveFolderScanner:
    """Escaneia o diretório local do Google Drive sincronizado (Drive Desktop, ex.:
    G:\\Meu Drive\\...) para encontrar aulas, PDFs e áudios. Usado quando
    STORAGE_BACKEND=local (padrão - continua sendo o comportamento no Windows da
    Jéssica, sem exigir nenhuma credencial nova)."""

    def __init__(self, base_path: Optional[str] = None):
        # Nome da pasta vem de config/config.yaml (bloco "semester") - editável pela
        # aba "⚙️ Configurações" do app.py, sem precisar mexer em código quando o
        # semestre trocar de pasta no Drive.
        self.base_path = Path(base_path) if base_path else Path(r"G:\Meu Drive") / settings.semester.drive_lessons_folder_name

    @staticmethod
    def _find_direct_materials(folder: Path) -> Dict[str, List[str]]:
        """Procura TODOS os slides (.pdf) e TODOS os áudios (.mp3/.wav/.m4a) direto
        dentro de `folder` (não recursivo) - uma aula pode ter o áudio dividido em
        várias partes (ex.: "Parte 1.m4a", "Parte 2.m4a"); pegar só o primeiro
        deixaria o resto de fora silenciosamente."""
        slides: List[str] = []
        audios: List[str] = []
        for file_path in sorted(folder.iterdir()):
            if file_path.is_file():
                ext = file_path.suffix.lower()
                if ext in SLIDE_EXTS:
                    slides.append(str(file_path))
                elif ext in AUDIO_EXTS:
                    audios.append(str(file_path))
        return {"slide": slides, "audio": audios}

    def _scan_lesson_folder(self, lesson_folder: Path) -> List[Dict[str, Any]]:
        """Retorna uma ou mais aulas encontradas dentro de `lesson_folder`.

        Caso comum (pasta "flat", ex.: UC16\\Aula 6): a própria pasta já tem
        slide/áudio direto - uma única aula, nome = nome da pasta (comportamento
        histórico, inalterado).

        Caso com subpastas por caso/tópico (ex.: UC05\\Aula 7\\Aula 7 - Caso 1,
        Aula 7 - Caso 2): quando `lesson_folder` NÃO tem slide/áudio direto mas
        alguma subpasta tem, cada subpasta com material vira uma aula
        INDEPENDENTE (nome = nome da subpasta) - cada uma gera seu próprio
        NotebookLM e sua própria linha na planilha.
        """
        direct = self._find_direct_materials(lesson_folder)
        if direct["slide"] or direct["audio"]:
            return [{
                "lesson_title": lesson_folder.name,
                "slide": direct["slide"],
                "audio": direct["audio"],
                "folder_path": str(lesson_folder),
            }]

        try:
            subfolders = [p for p in sorted(lesson_folder.iterdir()) if p.is_dir()]
        except Exception:
            subfolders = []

        sub_lessons = []
        for subfolder in subfolders:
            sub_materials = self._find_direct_materials(subfolder)
            if sub_materials["slide"] or sub_materials["audio"]:
                sub_lessons.append({
                    "lesson_title": subfolder.name,
                    "slide": sub_materials["slide"],
                    "audio": sub_materials["audio"],
                    "folder_path": str(subfolder),
                })

        if sub_lessons:
            return sub_lessons

        # Nem a pasta nem as subpastas têm slide/áudio - devolve a pasta como aula
        # "vazia" mesmo, mantendo o comportamento histórico (quem chama já sabe
        # lidar com isso: auto_pipeline.py pula aulas sem material detectado).
        return [{
            "lesson_title": lesson_folder.name,
            "slide": [],
            "audio": [],
            "folder_path": str(lesson_folder),
        }]

    def scan_local_lessons(self, unit_code: str) -> List[Dict[str, Any]]:
        """Varre a pasta da unidade curricular e retorna uma lista de aulas detectadas
        com seus arquivos. Pastas de aula sem material direto mas com subpastas por
        caso/tópico (cada uma com seu próprio slide/áudio) geram uma aula por
        subpasta, em vez de uma aula "vazia"."""
        lessons = []
        unit_dir = self.base_path / unit_code

        if not unit_dir.exists():
            logger.warning(f"Diretório da unidade {unit_code} não encontrado em: {unit_dir}")
            return lessons

        try:
            # Varre subpastas (ex: 'Aula 1', 'Aula 2', 'Aula 6', etc.)
            for lesson_folder in sorted(unit_dir.iterdir()):
                if lesson_folder.is_dir():
                    lessons.extend(self._scan_lesson_folder(lesson_folder))

            logger.info(f"Encontradas {len(lessons)} aulas para a unidade {unit_code}.")
        except Exception as e:
            logger.error(f"Erro ao escanear diretório local para {unit_code}: {e}")

        return lessons

    def resolve_apkg_output_path(self, unit_code: str, lesson_name_safe: str) -> Path:
        """Backend local: escreve direto dentro da pasta sincronizada do Drive -
        o addon do Anki (que varre essa mesma pasta) já enxerga o arquivo assim
        que ele é salvo, sem precisar de nenhum passo extra de "publicação"."""
        flashcards_root = Path(r"G:\Meu Drive") / settings.semester.drive_flashcards_folder_name
        return flashcards_root / unit_code / f"{lesson_name_safe}.apkg"

    def publish_flashcards_apkg(self, local_apkg_path: Path, unit_code: str, lesson_name: str) -> Dict[str, Any]:
        """Backend local: o .apkg já foi escrito pelo chamador direto dentro da
        pasta sincronizada do Drive (G:\\Meu Drive\\MedStudy_Flashcards\\<UC>\\...) -
        não há nada a "publicar", o addon do Anki já enxerga o arquivo. Existe só
        pra ter a mesma interface do backend de nuvem (DriveApiScanner)."""
        return {"success": True, "path": str(local_apkg_path), "url": None, "error": None}


class DriveApiScanner:
    """Mesma função que DriveFolderScanner, mas via Drive API (google-api-python-
    client) em vez de um diretório local sincronizado - usado quando
    STORAGE_BACKEND=cloud (Cloud Run não tem um "G:\\" montado).

    Baixa slide/áudio para um cache local temporário (settings.storage.temp_dir)
    antes de devolver os caminhos - o resto do pipeline (multimodal_processor,
    notebooklm_client) já espera caminhos de arquivo local e continua 100% igual,
    só quem entrega esses caminhos muda.
    """

    def __init__(self):
        from core.drive_api import drive_api_client
        self._client = drive_api_client
        # Nomes das pastas vêm de config/config.yaml (bloco "semester") - o mesmo
        # valor usado pelo backend local, editável sem mexer em código.
        self._lessons_root_name = settings.semester.drive_lessons_folder_name
        self._flashcards_root_name = settings.semester.drive_flashcards_folder_name
        self._lessons_root_id_cache: Optional[str] = None
        self._flashcards_root_id_cache: Optional[str] = None
        self._download_root = Path(settings.storage.temp_dir) / "drive_cache"

    def _lessons_root_id(self) -> Optional[str]:
        if self._lessons_root_id_cache is None:
            self._lessons_root_id_cache = self._client.find_root_folder(
                self._lessons_root_name, override_id=os.environ.get("GOOGLE_DRIVE_LESSONS_FOLDER_ID")
            )
        return self._lessons_root_id_cache

    def _flashcards_root_id(self) -> Optional[str]:
        if self._flashcards_root_id_cache is None:
            self._flashcards_root_id_cache = self._client.find_root_folder(
                self._flashcards_root_name, override_id=os.environ.get("GOOGLE_DRIVE_FLASHCARDS_FOLDER_ID")
            )
        return self._flashcards_root_id_cache

    def _find_direct_materials(self, folder_id: str, download_dir: Path) -> Dict[str, List[str]]:
        """Baixa TODOS os slides e TODOS os áudios da pasta (não só o primeiro) -
        mesmo motivo do backend local: uma aula pode ter o áudio em várias partes."""
        slides: List[str] = []
        audios: List[str] = []
        for entry in sorted(self._client.list_children(folder_id), key=lambda f: f["name"]):
            if entry["mimeType"] == "application/vnd.google-apps.folder":
                continue
            ext = Path(entry["name"]).suffix.lower()
            if ext in SLIDE_EXTS:
                slides.append(str(self._client.download_file(entry["id"], download_dir / entry["name"])))
            elif ext in AUDIO_EXTS:
                audios.append(str(self._client.download_file(entry["id"], download_dir / entry["name"])))
        return {"slide": slides, "audio": audios}

    def _list_subfolders(self, folder_id: str) -> List[Dict[str, Any]]:
        return [
            f for f in self._client.list_children(folder_id)
            if f["mimeType"] == "application/vnd.google-apps.folder"
        ]

    def _scan_lesson_folder(self, lesson_folder: Dict[str, Any], unit_code: str) -> List[Dict[str, Any]]:
        download_dir = self._download_root / unit_code / lesson_folder["name"]
        direct = self._find_direct_materials(lesson_folder["id"], download_dir)
        if direct["slide"] or direct["audio"]:
            return [{
                "lesson_title": lesson_folder["name"],
                "slide": direct["slide"],
                "audio": direct["audio"],
                "folder_path": lesson_folder["id"],
            }]

        sub_lessons = []
        for subfolder in sorted(self._list_subfolders(lesson_folder["id"]), key=lambda f: f["name"]):
            sub_download_dir = self._download_root / unit_code / lesson_folder["name"] / subfolder["name"]
            sub_materials = self._find_direct_materials(subfolder["id"], sub_download_dir)
            if sub_materials["slide"] or sub_materials["audio"]:
                sub_lessons.append({
                    "lesson_title": subfolder["name"],
                    "slide": sub_materials["slide"],
                    "audio": sub_materials["audio"],
                    "folder_path": subfolder["id"],
                })

        if sub_lessons:
            return sub_lessons

        return [{
            "lesson_title": lesson_folder["name"],
            "slide": [],
            "audio": [],
            "folder_path": lesson_folder["id"],
        }]

    def scan_local_lessons(self, unit_code: str) -> List[Dict[str, Any]]:
        """Mesmo contrato de DriveFolderScanner.scan_local_lessons - nome do método
        mantido (mesmo rodando via API, não "local") pra não precisar mudar quem
        chama (auto_pipeline.py)."""
        lessons: List[Dict[str, Any]] = []
        lessons_root_id = self._lessons_root_id()
        if not lessons_root_id:
            return lessons

        try:
            unit_folder = next(
                (f for f in self._list_subfolders(lessons_root_id) if f["name"] == unit_code), None
            )
            if not unit_folder:
                logger.warning(f"Pasta da unidade {unit_code} não encontrada dentro de '{self._lessons_root_name}' no Drive.")
                return lessons

            for lesson_folder in sorted(self._list_subfolders(unit_folder["id"]), key=lambda f: f["name"]):
                lessons.extend(self._scan_lesson_folder(lesson_folder, unit_code))

            logger.info(f"Encontradas {len(lessons)} aulas para a unidade {unit_code} (Drive API).")
        except Exception as e:
            logger.error(f"Erro ao escanear o Drive via API para {unit_code}: {e}")

        return lessons

    def resolve_apkg_output_path(self, unit_code: str, lesson_name_safe: str) -> Path:
        """Backend cloud: não existe "G:\\" no container - o .apkg é primeiro
        escrito num diretório temporário local, e só depois enviado pro Drive de
        verdade por publish_flashcards_apkg (chamado logo em seguida pelo
        orchestrator)."""
        return self._download_root.parent / "flashcards_out" / unit_code / f"{lesson_name_safe}.apkg"

    def publish_flashcards_apkg(self, local_apkg_path: Path, unit_code: str, lesson_name: str) -> Dict[str, Any]:
        """Backend cloud: sobe o .apkg (já escrito localmente pelo chamador num
        diretório temporário) pra dentro de MedStudy_Flashcards/<UC>/ no Drive,
        criando a subpasta da UC se ainda não existir - substitui um .apkg
        anterior com o mesmo nome em vez de duplicar (mesma semântica do backend
        local: regerou, sobrescreve)."""
        flashcards_root_id = self._flashcards_root_id()
        if not flashcards_root_id:
            return {"success": False, "path": str(local_apkg_path), "url": None,
                     "error": f"pasta '{self._flashcards_root_name}' não encontrada/compartilhada no Drive"}

        uc_folder_id = self._client.find_or_create_folder(flashcards_root_id, unit_code)
        if not uc_folder_id:
            return {"success": False, "path": str(local_apkg_path), "url": None,
                     "error": f"não consegui achar/criar a subpasta '{unit_code}' em '{self._flashcards_root_name}' no Drive"}

        result = self._client.upload_file(local_apkg_path, uc_folder_id, filename=local_apkg_path.name)
        return {
            "success": result["success"],
            "path": str(local_apkg_path),
            "url": result.get("url"),
            "error": result.get("error"),
        }


# STORAGE_BACKEND=local (padrão, Windows/Drive Desktop) ou STORAGE_BACKEND=cloud
# (Cloud Run, Drive API) - decide qual implementação `drive_sync` expõe. O resto
# do pipeline (auto_pipeline.py, orchestrator.py) usa sempre `drive_sync`, sem
# saber qual backend está por trás.
_STORAGE_BACKEND = os.environ.get("STORAGE_BACKEND", "local").strip().lower()

if _STORAGE_BACKEND == "cloud":
    logger.info("STORAGE_BACKEND=cloud - usando Drive API (core.drive_api) em vez do Drive Desktop local.")
    drive_sync = DriveApiScanner()
else:
    drive_sync = DriveFolderScanner()
