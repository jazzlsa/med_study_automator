import os
import re
from pathlib import Path
from typing import Dict, List, Optional
from utils.logger import logger


class DriveFolderScanner:
    """Varre o Drive local para identificar automaticamente todas as UCs, Aulas e Arquivos."""

    def __init__(self, default_drive_path: Optional[str] = None):
        self.drive_path = Path(default_drive_path) if default_drive_path else Path("G:/Meu Drive/MedStudy_Aulas")
        if not self.drive_path.exists():
            for p in [Path("G:/Meu Drive"), Path("G:/My Drive"), Path("data/raw")]:
                if p.exists():
                    self.drive_path = p
                    break

    def scan_ucs_and_lessons(self, base_path: Optional[Path] = None) -> Dict[str, List[Dict]]:
        """
        Lê a pasta raiz e retorna a árvore completa:
        {
          "UC16": [
             {"lesson_folder": "Aula 6", "slide": Path(...), "audio": Path(...), "files": [...]}
          ]
        }
        """
        root = base_path if base_path else self.drive_path
        if not root or not root.exists():
            return {}

        structure = {}

        # 1. Procura pastas de UCs (UC04, UC16, UC29, etc)
        for uc_dir in sorted(root.iterdir()):
            if not uc_dir.is_dir() or uc_dir.name.startswith("."):
                continue

            uc_name = uc_dir.name
            structure[uc_name] = []

            # 2. Procura pastas de Aulas dentro da UC
            for item in sorted(uc_dir.iterdir()):
                if item.is_dir() and not item.name.startswith("."):
                    lesson_name = item.name
                    slide_file = None
                    audio_file = None
                    all_files = []

                    for f in item.rglob("*"):
                        if f.is_file():
                            all_files.append(f)
                            suffix = f.suffix.lower()
                            if suffix in [".pdf", ".pptx"] and not slide_file:
                                slide_file = f
                            elif suffix in [".mp3", ".m4a", ".wav", ".mp4", ".aac", ".ogg"] and not audio_file:
                                audio_file = f

                    structure[uc_name].append({
                        "lesson_title": lesson_name,
                        "folder_path": item,
                        "slide": slide_file,
                        "audio": audio_file,
                        "total_files": len(all_files),
                        "file_names": [f.name for f in all_files]
                    })
                
                # Caso os arquivos estejam soltos direto dentro da pasta da UC
                elif item.is_file():
                    suffix = item.suffix.lower()
                    if suffix in [".pdf", ".mp3", ".m4a", ".wav"]:
                        lesson_title = item.stem
                        existing = next((l for l in structure[uc_name] if l["lesson_title"] == lesson_title), None)
                        if not existing:
                            existing = {
                                "lesson_title": lesson_title,
                                "folder_path": uc_dir,
                                "slide": item if suffix in [".pdf", ".pptx"] else None,
                                "audio": item if suffix in [".mp3", ".m4a", ".wav"] else None,
                                "total_files": 1,
                                "file_names": [item.name]
                            }
                            structure[uc_name].append(existing)

        return structure


drive_sync = DriveFolderScanner()