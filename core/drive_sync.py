import os
from pathlib import Path
from typing import List, Dict, Any, Optional
from utils.logger import logger

class DriveFolderScanner:
    """Escaneia o diretório local do Google Drive sincronizado para encontrar aulas, PDFs e áudios."""

    def __init__(self, base_path: Optional[str] = None):
        # Caminho padrão baseado no seu ambiente (G:\Meu Drive\MedStudy_Aulas)
        self.base_path = Path(base_path) if base_path else Path(r"G:\Meu Drive\MedStudy_Aulas")

    def scan_local_lessons(self, unit_code: str) -> List[Dict[str, Any]]:
        """Varre a pasta da unidade curricular e retorna uma lista de aulas detectadas com seus arquivos."""
        lessons = []
        unit_dir = self.base_path / unit_code

        if not unit_dir.exists():
            logger.warning(f"Diretório da unidade {unit_code} não encontrado em: {unit_dir}")
            return lessons

        try:
            # Varre subpastas (ex: 'Aula 1', 'Aula 2', 'Aula 6', etc.)
            for lesson_folder in sorted(unit_dir.iterdir()):
                if lesson_folder.is_dir():
                    lesson_title = lesson_folder.name
                    slide_path = None
                    audio_path = None

                    # Procura por arquivos de slide (.pdf) e áudio (.mp3) dentro da pasta da aula
                    for file_path in lesson_folder.iterdir():
                        if file_path.is_file():
                            ext = file_path.suffix.lower()
                            if ext == ".pdf" and not slide_path:
                                slide_path = str(file_path)
                            elif ext in [".mp3", ".wav", ".m4a"] and not audio_path:
                                audio_path = str(file_path)

                    lessons.append({
                        "lesson_title": lesson_title,
                        "slide": slide_path,
                        "audio": audio_path,
                        "folder_path": str(lesson_folder)
                    })

            logger.info(f"Encontradas {len(lessons)} aulas para a unidade {unit_code}.")
        except Exception as e:
            logger.error(f"Erro ao escanear diretório local para {unit_code}: {e}")

        return lessons

drive_sync = DriveFolderScanner()