"""One-off: regenera transcrição + Estúdio das aulas processadas automaticamente
ANTES do prompt de transcrição ter sido corrigido pra ipsis litteris (o prompt
antigo pedia um texto "fluido e estruturado", que na prática saía como um resumo
disfarçado - ver core/multimodal_processor.py).

Diferente de rodar o pipeline normal de novo: aqui a fonte "transcricao_aula.txt"
já existente no notebook é APAGADA antes de subir a nova (o fluxo normal só
verifica presença por NOME e pularia o upload, deixando o conteúdo velho parado
lá) e TODOS os artefatos do Estúdio são apagados e regerados do zero, pra
refletirem o texto literal novo em vez do resumo antigo.

TARGETS abaixo são as únicas aulas feitas pelo pipeline automático antes da
correção do prompt (a UC16 Aula 6 foi feita manualmente pela Beatriz - não
faz parte disso, nunca deve ser tocada por este script).

Uso (dentro do container/ambiente cloud, mesmas env vars do pipeline normal):
  python regenerate_transcripts.py
"""
from pathlib import Path

from core.anki_flashcards import build_flashcards_apkg
from core.drive_sync import drive_sync
from core.multimodal_processor import multimodal_processor
from core.notebooklm_client import notebooklm_client
from core.orchestrator import _safe_filename
from core.sheets_client import sheets_client
from database.db import db_manager
from utils.logger import logger

TARGETS = [
    ("UC05", "Aula 7 - Caso 1"),
    ("UC05", "Aula 7 - Caso 2"),
    ("UC17", "Aula 06 - Herança multifatorial (Exercícios) "),
    ("UC17", "Aula 06 - Herança multifatorial (Teórica) "),
]

TRANSCRIPT_SOURCE_TITLE = "transcricao_aula.txt"


def _find_lesson_files(unit_code: str, lesson_name: str):
    """Reescaneia o Drive pra achar slide/áudio da aula (mesmo contrato usado por
    auto_pipeline.py) - não dá pra reaproveitar o que ficou em data/temp da
    execução original porque esse diretório é efêmero (some com o container)."""
    lessons = drive_sync.scan_local_lessons(unit_code)
    for lesson in lessons:
        if lesson["lesson_title"].strip() == lesson_name.strip():
            return lesson.get("slide") or [], lesson.get("audio") or []
    return [], []


def regenerate_one(unit_code: str, lesson_name: str) -> bool:
    logger.info(f"=== Regenerando [{unit_code}] {lesson_name} ===")
    row = db_manager.get_lesson_status(unit_code, lesson_name)
    notebook_id = row.get("notebook_id") if row else None
    if not notebook_id or notebook_id == "N/A":
        logger.error(f"[{unit_code}] '{lesson_name}': sem notebook_id válido no banco - pulando.")
        return False

    if not notebooklm_client.notebook_exists(notebook_id):
        logger.error(f"[{unit_code}] '{lesson_name}': notebook {notebook_id} não existe mais - pulando (precisa reprocessar do zero).")
        return False

    slide_paths, audio_paths = _find_lesson_files(unit_code, lesson_name)
    if not slide_paths and not audio_paths:
        logger.error(f"[{unit_code}] '{lesson_name}': não achei slide/áudio no Drive - pulando.")
        return False

    slides = [Path(p) for p in slide_paths]
    audios = [Path(p) for p in audio_paths]

    logger.info(f"Reextraindo via Gemini com o prompt de transcrição literal (slides={[s.name for s in slides]}, audios={[a.name for a in audios]})...")
    gemini_result = multimodal_processor.analyze_lesson_materials(
        slide_paths=slides, audio_paths=audios, lesson_name=lesson_name, unit_code=unit_code
    )
    if not gemini_result.get("success") or not gemini_result.get("transcript_path"):
        logger.error(f"[{unit_code}] '{lesson_name}': falha ao regerar a transcrição via Gemini: {gemini_result.get('error')} - pulando.")
        return False

    transcript_path = gemini_result["transcript_path"]
    tema = gemini_result.get("tema")

    # Apaga a fonte de transcrição antiga (resumida) antes de subir a nova -
    # senão add_source_to_notebook pularia por já achar o título presente.
    removed = notebooklm_client.delete_sources_by_title(notebook_id, TRANSCRIPT_SOURCE_TITLE)
    logger.info(f"Fonte(s) de transcrição antiga removida(s): {removed}")

    add_result = notebooklm_client.add_source_to_notebook(notebook_id, transcript_path)
    if not add_result["success"]:
        logger.error(f"[{unit_code}] '{lesson_name}': falha ao subir a transcrição nova: {add_result['error']} - pulando Estúdio.")
        return False

    ready_result = notebooklm_client.wait_for_sources_ready(notebook_id)
    if not ready_result["success"]:
        logger.error(f"[{unit_code}] '{lesson_name}': fontes não ficaram prontas ({ready_result.get('error')}) - pulando Estúdio.")
        return False

    deleted_artifacts = notebooklm_client.delete_all_artifacts(notebook_id)
    logger.info(f"Artefatos antigos apagados: {deleted_artifacts} - regerando o Estúdio completo com a transcrição literal...")
    studio_result = notebooklm_client.generate_studio_artifacts(notebook_id)
    if not studio_result["success"]:
        failed = [k for k, r in studio_result["artifacts"].items() if not r["success"]]
        logger.error(f"[{unit_code}] '{lesson_name}': falha ao regerar artefato(s): {failed}")

    # Flashcards (.apkg) regerados a partir do MESMO gemini_result novo - sobrescreve
    # o arquivo anterior no Drive (mesma semântica de sempre: regerou, sobrescreve).
    flashcards = gemini_result.get("flashcards") or []
    if flashcards:
        apkg_path = drive_sync.resolve_apkg_output_path(unit_code, _safe_filename(lesson_name))
        apkg_result = build_flashcards_apkg(flashcards, unit_code, lesson_name, apkg_path)
        if apkg_result["success"]:
            publish_result = drive_sync.publish_flashcards_apkg(apkg_path, unit_code, lesson_name)
            if publish_result["success"]:
                logger.info(f".apkg regerado e publicado no Drive: {publish_result.get('url') or publish_result['path']}")
            else:
                logger.error(f"Falha ao publicar .apkg regerado no Drive: {publish_result['error']}")
        else:
            logger.error(f"Falha ao regerar .apkg de flashcards: {apkg_result['error']}")

    notebook_url = f"https://notebook.google.com/notebook/{notebook_id}"
    sheets_client.update_lesson_link(unit_code, lesson_name, notebook_url, tema=tema)

    db_manager.mark_lesson_completed(
        unit_code=unit_code, lesson_name=lesson_name, notebook_id=notebook_id,
        status="success", details=None,
    )
    logger.info(f"[{unit_code}] '{lesson_name}': transcrição + Estúdio regerados com sucesso.")
    return True


def run() -> int:
    logger.info("=" * 70)
    logger.info("Regenerando transcrição literal + Estúdio das aulas antigas")
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
