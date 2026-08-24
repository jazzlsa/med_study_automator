import hashlib
from pathlib import Path
from typing import Optional, Union


def compute_file_hash(file_path: Union[str, Path], chunk_size: int = 65536) -> str:
    """Calcula o hash MD5 de um arquivo lendo em blocos para economia de memória."""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Arquivo não encontrado para cálculo de hash: {file_path}")

    md5 = hashlib.md5()
    with open(path, "rb") as f:
        while chunk := f.read(chunk_size):
            md5.update(chunk)
    return md5.hexdigest()


def compute_content_hash(
    slide_path: Union[str, Path], audio_path: Optional[Union[str, Path]] = None
) -> str:
    """Gera um hash único combinando o slide e o áudio da aula."""
    slide_hash = compute_file_hash(slide_path)
    
    if audio_path and Path(audio_path).exists():
        audio_hash = compute_file_hash(audio_path)
        combined = f"{slide_hash}_{audio_hash}"
        return hashlib.md5(combined.encode("utf-8")).hexdigest()
    
    return slide_hash