"""One-off: regenera os flashcards (.apkg) das aulas que estão no Drive, com pelo
menos MIN_FLASHCARDS_PER_LESSON cartões cada (ver core/multimodal_processor.py) e
imagem de slide embutida quando o Gemini indicar que há um elemento visual
relevante pro card (ver core/anki_flashcards.py + core/slide_extractor.py).

Só mexe nos flashcards - NÃO toca no NotebookLM (notebook/fontes/Estúdio) nem na
planilha, só reextrai via Gemini e regera/republica o .apkg. Roda local
(STORAGE_BACKEND=local, Drive Desktop em G:\\Meu Drive).

UC16 Aula 6 (notebook feito manualmente pela Beatriz) fica de fora de propósito -
não editar TARGETS pra incluí-la sem confirmar com o usuário antes.

Uso: venv\\Scripts\\python.exe regenerate_flashcards.py
"""
from pathlib import Path

from core.anki_flashcards import build_flashcards_apkg
from core.drive_sync import drive_sync
from core.multimodal_processor import multimodal_processor
from core.orchestrator import _safe_filename
from utils.logger import logger

TARGETS = [
    ("UC05", "Aula 7 - Caso 1"),
    ("UC05", "Aula 7 - Caso 2"),
    ("UC17", "Aula 06 - Herança multifatorial (Exercícios)"),
    ("UC17", "Aula 06 - Herança multifatorial (Teórica)"),
]


def _find_lesson_files(unit_code: str, lesson_name: str):
    """Reescaneia o Drive (Desktop, local) pra achar slide(s)/áudio(s) da aula -
    mesmo contrato usado por auto_pipeline.py."""
    lessons = drive_sync.scan_local_lessons(unit_code)
    for lesson in lessons:
        if lesson["lesson_title"].strip() == lesson_name.strip():
            return lesson.get("slide") or [], lesson.get("audio") or []
    return [], []


def regenerate_one(unit_code: str, lesson_name: str) -> bool:
    logger.info(f"=== Regenerando flashcards: [{unit_code}] {lesson_name} ===")

    slide_paths, audio_paths = _find_lesson_files(unit_code, lesson_name)
    if not slide_paths and not audio_paths:
        logger.error(f"[{unit_code}] '{lesson_name}': não achei slide/áudio no Drive - pulando.")
        return False

    slides = [Path(p) for p in slide_paths]
    audios = [Path(p) for p in audio_paths]
    logger.info(f"Slides: {[s.name for s in slides]} | Áudios: {[a.name for a in audios]}")

    gemini_result = multimodal_processor.analyze_lesson_materials(
        slide_paths=slides, audio_paths=audios, lesson_name=lesson_name, unit_code=unit_code
    )
    if not gemini_result.get("success"):
        logger.error(f"[{unit_code}] '{lesson_name}': falha no Gemini: {gemini_result.get('error')} - pulando.")
        return False

    flashcards = gemini_result.get("flashcards") or []
    if not flashcards:
        logger.error(f"[{unit_code}] '{lesson_name}': Gemini não retornou nenhum flashcard - pulando.")
        return False

    apkg_path = drive_sync.resolve_apkg_output_path(unit_code, _safe_filename(lesson_name))
    apkg_result = build_flashcards_apkg(flashcards, unit_code, lesson_name, apkg_path)
    if not apkg_result["success"]:
        logger.error(f"[{unit_code}] '{lesson_name}': falha ao gerar .apkg: {apkg_result['error']}")
        return False

    publish_result = drive_sync.publish_flashcards_apkg(apkg_path, unit_code, lesson_name)
    if not publish_result["success"]:
        logger.error(f"[{unit_code}] '{lesson_name}': falha ao publicar .apkg: {publish_result['error']}")
        return False

    total = apkg_result["count_mc"] + apkg_result["count_vf"]
    logger.info(
        f"[{unit_code}] '{lesson_name}': {apkg_result['count_mc']} MC + {apkg_result['count_vf']} VF "
        f"({total} total) -> {publish_result.get('url') or publish_result['path']}"
    )
    return True


def run() -> int:
    logger.info("=" * 70)
    logger.info("Regenerando flashcards (.apkg) das aulas no Drive")
    logger.info("=" * 70)
    ok, failed = 0, []
    for unit_code, lesson_name in TARGETS:
        try:
            if regenerate_one(unit_code, lesson_name):
                ok += 1
            else:
                failed.append((unit_code, lesson_name))
        except Exception as e:
            logger.error(f"[{unit_code}] '{lesson_name}': erro não tratado: {e}")
            failed.append((unit_code, lesson_name))

    logger.info("-" * 70)
    logger.info(f"Concluído: {ok} regenerada(s) com sucesso, {len(failed)} falharam.")
    for unit_code, lesson_name in failed:
        logger.error(f"  FALHOU: [{unit_code}] {lesson_name}")
    return 0 if not failed else 1


if __name__ == "__main__":
    import sys
    sys.exit(run())
