import os
from pathlib import Path
from typing import List, Dict, Optional
from utils.logger import logger

class DriveSyncManager:
    """Gerencia a localização de arquivos de aulas a partir do Drive mapeado localmente."""

    def __init__(self, default_drive_path: Optional[str] = None):
        # Tenta detectar caminhos comuns do Google Drive no Windows
        possible_paths = [
            Path("G:/Meu Drive"),
            Path("G:/My Drive"),
            Path(os.path.expanduser("~/Google Drive")),
            Path(os.path.expanduser("~/Meu Drive")),
            Path("data/raw")
        ]
        
        self.drive_path = Path(default_drive_path) if default_drive_path else None
        if not self.drive_path or not self.drive_path.exists():
            for p in possible_paths:
                if p.exists():
                    self.drive_path = p
                    break
        
        if not self.drive_path:
            self.drive_path = Path("data/raw")
            self.drive_path.mkdir(parents=True, exist_ok=True)

    def find_lesson_files(self, uc_name: str, lesson_title: str) -> Dict[str, Optional[Path]]:
        """Busca arquivos de slide (.pdf) e áudio (.mp3, .m4a, .wav) correspondentes à aula."""
        result = {"slide": None, "audio": None}
        
        if not self.drive_path.exists():
            return result

        # Procura em subpastas da UC ou na raiz
        search_dirs = [self.drive_path]
        uc_dir = self.drive_path / uc_name
        if uc_dir.exists():
            search_dirs.insert(0, uc_dir)

        # Normaliza termos para busca
        keywords = [k.lower() for k in lesson_title.split() if len(k) > 3]

        for s_dir in search_dirs:
            for file_path in s_dir.rglob("*"):
                if file_path.is_file():
                    fname = file_path.name.lower()
                    
                    # Checa se é PDF de slide
                    if file_path.suffix.lower() == ".pdf" and not result["slide"]:
                        if any(k in fname for k in keywords):
                            result["slide"] = file_path

                    # Checa se é arquivo de áudio
                    if file_path.suffix.lower() in [".mp3", ".m4a", ".wav", ".aac"] and not result["audio"]:
                        if any(k in fname for k in keywords):
                            result["audio"] = file_path

        return result

    def list_files_in_folder(self, folder_path: Path) -> List[Path]:
        """Lista todos os PDFs e áudios dentro de um diretório selecionado."""
        p = Path(folder_path)
        if not p.exists():
            return []
        valid_exts = [".pdf", ".mp3", ".m4a", ".wav", ".aac"]
        return [f for f in p.rglob("*") if f.suffix.lower() in valid_exts]

drive_sync = DriveSyncManager()