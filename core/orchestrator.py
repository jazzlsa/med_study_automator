import time
from pathlib import Path
from typing import List, Union
from core.multimodal_processor import multimodal_processor
from core.anki_generator import anki_generator
from database.db import db_manager
from utils.logger import logger
import requests
import json

class Orchestrator:
    """Orquestra o pipeline completo: Varredura de Pasta ➔ Gemini ➔ Anki & Banco de Dados."""

    def __init__(self):
        pass

    def _send_to_anki(self, apkg_path: Path) -> bool:
        """Envia o baralho gerado para o Anki via AnkiConnect local."""
        if not apkg_path.exists():
            return False
        
        request_json = json.dumps({
            "action": "importPackage",
            "version": 6,
            "params": {"path": str(apkg_path.absolute())}
        })
        try:
            response = requests.post("http://localhost:8765", data=request_json, timeout=5)
            res_obj = response.json()
            if res_obj.get("error") is None:
                logger.info("Baralho importado com sucesso no Anki!")
                return True
            else:
                logger.warning(f"Erro do AnkiConnect: {res_obj.get('error')}")
                return False
        except Exception:
            logger.warning("Anki fechado ou AnkiConnect não detectado. O arquivo .apkg foi salvo para download manual.")
            return False

    def process_lesson(
        self,
        unit_code: str,
        lesson_name: str,
        slide_path: Union[Path, List[Path], None] = None,
        audio_path: Union[Path, List[Path], None] = None,
        force_reprocess: bool = True,
        sync_anki: bool = True
    ) -> dict:
        start_time = time.time()
        logger.info(f"Iniciando processamento da aula: [{unit_code}] {lesson_name}")

        # Padroniza para listas de arquivos
        slides = []
        if isinstance(slide_path, list):
            slides = [p for p in slide_path if p and Path(p).exists()]
        elif slide_path and Path(slide_path).exists():
            slides = [Path(slide_path)]

        audios = []
        if isinstance(audio_path, list):
            audios = [p for p in audio_path if p and Path(p).exists()]
        elif audio_path and Path(audio_path).exists():
            audios = [Path(audio_path)]

        # 1. Análise Multimodal com Gemini
        gemini_result = multimodal_processor.analyze_lesson_materials(
            slide_paths=slides,
            audio_paths=audios,
            lesson_name=lesson_name,
            unit_code=unit_code
        )

        summary_text = gemini_result.get("summary", "")
        cards_data = gemini_result.get("flashcards", [])

        # 2. Geração do Pacote .apkg
        safe_lesson_name = "".join(c for c in lesson_name if c.isalnum() or c in (' ', '_', '-')).strip()
        apkg_filename = f"{unit_code} - {safe_lesson_name}.apkg"
        apkg_path = Path("data/output") / apkg_filename
        
        deck_title = f"{unit_code} :: {lesson_name}"
        anki_generator.generate_apkg(cards_data, deck_title, apkg_path)

        # 3. Sincronização com o Anki
        if sync_anki:
            self._send_to_anki(apkg_path)

        # 4. Salvamento no Banco Local (SQLite)
        execution_time = time.time() - start_time
        lesson_id = db_manager.save_lesson_record(
            unit_code=unit_code,
            lesson_name=lesson_name,
            summary=summary_text,
            cards_count=len(cards_data),
            apkg_path=str(apkg_path),
            execution_time=execution_time,
            prompt_tokens=1500,
            completion_tokens=800
        )

        return {
            "lesson_id": lesson_id,
            "cards_count": len(cards_data),
            "apkg_path": str(apkg_path),
            "execution_time": execution_time
        }

orchestrator = Orchestrator()