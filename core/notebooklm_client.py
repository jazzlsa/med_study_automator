import subprocess
import json
from pathlib import Path
from typing import Optional
from utils.logger import logger

class NotebookLMClient:
    """Interface para interagir com o NotebookLM via CLI do notebooklm-py com geração automática de estúdio."""

    def __init__(self):
        pass

    def _run_cli(self, args: list) -> Optional[dict]:
        try:
            cmd = ["notebooklm"] + args + ["--json"]
            logger.info(f"Executando CLI: {' '.join(cmd)}")
            result = subprocess.run(
                cmd, 
                capture_output=True, 
                text=True, 
                encoding='utf-8', 
                check=True, 
                timeout=25
            )
            if result.stdout.strip():
                return json.loads(result.stdout)
            return {}
        except Exception as e:
            logger.error(f"Aviso CLI NotebookLM: {e}")
            return None

    def create_notebook(self, title: str) -> Optional[str]:
        try:
            data = self._run_cli(["create", title])
            if data:
                if "notebook" in data and isinstance(data["notebook"], dict):
                    return data["notebook"].get("id")
                elif "id" in data:
                    return data.get("id")
        except Exception:
            pass
        return None

    def add_source_to_notebook(self, notebook_id: str, file_path: Path) -> bool:
        """Adiciona um arquivo local como fonte e dispara a geração automática no Estúdio."""
        try:
            # 1. Define o notebook ativo
            use_cmd = ["notebooklm", "use", notebook_id]
            subprocess.run(use_cmd, capture_output=True, text=True, encoding='utf-8', timeout=10)

            # 2. Adiciona a fonte
            cmd = ["notebooklm", "source", "add", str(file_path.absolute())]
            logger.info(f"Adicionando fonte via CLI: {' '.join(cmd)}")
            subprocess.run(
                cmd, 
                capture_output=True, 
                text=True, 
                encoding='utf-8', 
                check=True, 
                timeout=25
            )

            # 3. Dispara a geração dos principais artefatos na aba Estúdio (Áudio e Relatório/Guia)
            logger.info("Solicitando geração automática de áudio e guia de estudo no NotebookLM...")
            subprocess.run(["notebooklm", "generate", "audio"], capture_output=True, text=True, encoding='utf-8', timeout=10)
            subprocess.run(["notebooklm", "generate", "report", "--format", "study-guide"], capture_output=True, text=True, encoding='utf-8', timeout=10)

            return True
        except Exception as e:
            logger.warning(f"Erro ao injetar fonte ou gerar estúdio para {file_path.name}: {e}")
            return False

notebooklm_client = NotebookLMClient()