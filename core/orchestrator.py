import time
from pathlib import Path
from typing import Optional, Dict, Any

from config.settings import settings
from database.db import db_manager
from utils.hasher import compute_content_hash
from utils.logger import logger
from core.slide_extractor import slide_extractor
from core.gemini_client import gemini_client
from core.anki_generator import anki_compiler


class StudyPipelineOrchestrator:
    """Orquestra o fluxo completo de processamento de materiais médicos."""

    def process_lesson(
        self,
        unit_code: str,
        lesson_name: str,
        slide_path: Optional[Path] = None,
        audio_path: Optional[Path] = None,
        force_reprocess: bool = False,
        sync_anki: bool = True,
    ) -> Dict[str, Any]:
        start_time = time.time()
        logger.info(f"Iniciando pipeline: [{unit_code}] {lesson_name}")

        slide_file = Path(slide_path) if slide_path else None
        audio_file = Path(audio_path) if audio_path else None

        if not slide_file and not audio_file:
            raise ValueError("É necessário fornecer ao menos o slide (PDF) ou o áudio da aula.")

        # 1. Verificação de Idempotência via Hash
        content_hash = compute_content_hash(slide_file, audio_file)
        existing_lesson = db_manager.get_lesson_by_hash(content_hash)

        if existing_lesson and not force_reprocess:
            logger.warning(
                f"⏭️ Aula já processada anteriormente em {existing_lesson['created_at']}. "
                f"Baralho existente: {existing_lesson['apkg_path']}. Use --force para reprocessar."
            )
            return {
                "status": "skipped",
                "reason": "already_processed",
                "lesson_id": existing_lesson["id"],
                "apkg_path": existing_lesson["apkg_path"],
            }

        # 2. Extração de Imagens dos Slides
        media_images = []
        if slide_file and slide_file.exists():
            try:
                media_images = slide_extractor.extract_slide_pages(slide_file, lesson_name)
            except Exception as e:
                logger.error(f"Falha na extração de slides: {e}")

        # 3. Processamento Multimodal com Gemini
        result, p_tok, c_tok = gemini_client.process_lesson_materials(
            unit_name=unit_code,
            lesson_title=lesson_name,
            slide_path=slide_file,
            audio_path=audio_file,
        )

        # 4. Compilação do Pacote Anki (.apkg)
        deck_category = f"Medicina::{unit_code}"
        apkg_path = anki_compiler.compile_apkg(
            deck_name=deck_category,
            lesson_name=lesson_name,
            flashcards=result.flashcards,
            media_files=media_images,
        )

        # 5. Sincronização direta com AnkiConnect
        if sync_anki:
            anki_compiler.sync_with_ankiconnect(apkg_path)

        # 6. Registro no SQLite e Métricas
        elapsed_sec = time.time() - start_time
        lesson_id = db_manager.save_lesson(
            unit_code=unit_code,
            lesson_name=lesson_name,
            content_hash=content_hash,
            cards_count=len(result.flashcards),
            apkg_path=str(apkg_path.resolve()),
        )

        db_manager.log_metrics(
            lesson_id=lesson_id,
            prompt_tokens=p_tok,
            completion_tokens=c_tok,
            execution_time_sec=elapsed_sec,
            cost_usd=0.0,
        )

        logger.success(
            f" Pipeline concluído em {elapsed_sec:.2f}s! {len(result.flashcards)} cards gerados."
        )

        return {
            "status": "success",
            "lesson_id": lesson_id,
            "apkg_path": str(apkg_path),
            "cards_count": len(result.flashcards),
            "execution_time": elapsed_sec,
            "summary": result.summary,
        }


orchestrator = StudyPipelineOrchestrator()