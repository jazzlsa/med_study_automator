import time
from pathlib import Path
from typing import List, Union, Optional, Any
from core.multimodal_processor import multimodal_processor
from core.notebooklm_client import notebooklm_client
from core.sheets_client import sheets_client
from database.db import db_manager
from utils.logger import logger

class Orchestrator:
    """Orquestra o pipeline focado no NotebookLM, transcrições e Sheets."""

    def __init__(self):
        pass

    def process_lesson(
        self,
        unit_code: str,
        lesson_name: str,
        slide_paths: Optional[Union[str, Path, List[Union[str, Path]]]] = None,
        audio_paths: Optional[Union[str, Path, List[Union[str, Path]]]] = None,
        slide_path: Optional[Union[str, Path, List[Union[str, Path]]]] = None,
        audio_path: Optional[Union[str, Path, List[Union[str, Path]]]] = None,
        force_reprocess: Optional[bool] = None,
        **kwargs: Any
    ) -> bool:
        """Processa a aula, cria o NotebookLM, gera o Estúdio e registra na planilha."""
        try:
            logger.info(f"Iniciando processamento da aula: [{unit_code}] {lesson_name}")

            raw_slides = slide_paths if slide_paths is not None else slide_path
            raw_audios = audio_paths if audio_paths is not None else audio_path

            slides = [Path(raw_slides)] if isinstance(raw_slides, (str, Path)) else [Path(p) for p in raw_slides if p] if isinstance(raw_slides, list) else []
            audios = [Path(raw_audios)] if isinstance(raw_audios, (str, Path)) else [Path(p) for p in raw_audios if p] if isinstance(raw_audios, list) else []

            # 1. Cria o NotebookLM para a aula
            notebook_title = f"{unit_code} - {lesson_name}"
            logger.info(f"Criando NotebookLM para: {notebook_title}")
            notebook_id = notebooklm_client.create_notebook(notebook_title)

            # 2. Injeta as fontes no NotebookLM
            if notebook_id:
                for slide in slides:
                    notebooklm_client.add_source_to_notebook(notebook_id, slide)
                for audio in audios:
                    notebooklm_client.add_source_to_notebook(notebook_id, audio)
                
                notebook_url = f"https://notebooklm.google.com/notebook/{notebook_id}"
                logger.info(f"NotebookLM pronto! Link: {notebook_url}")

                # 3. Registra o link na Planilha do Google Sheets
                sheets_client.update_lesson_link(unit_code, lesson_name, notebook_url)

            # 4. Extrai transcrição e resumos estruturados via Gemini
            logger.info("Extraindo resumos e transcrição via Gemini...")
            multimodal_processor.analyze_lesson_materials(
                slide_paths=slides,
                audio_paths=audios,
                lesson_name=lesson_name,
                unit_code=unit_code
            )

            # 5. Salva no banco de dados local
            db_manager.mark_lesson_completed(
                unit_code=unit_code,
                lesson_name=lesson_name,
                notebook_id=notebook_id or "N/A"
            )

            logger.info(f"Pipeline concluído com sucesso para a aula {lesson_name}!")
            return True

        except Exception as e:
            logger.error(f"Erro crítico no orquestrador da aula {lesson_name}: {e}")
            return False

orchestrator = Orchestrator()