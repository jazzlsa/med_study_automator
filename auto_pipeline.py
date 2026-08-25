"""Script de automação do MedStudy Automator.

Percorre TODAS as UCs conhecidas (AVAILABLE_UCS, centralizado em core/sheets_sync.py),
detecta aulas novas via pasta local do Google Drive (core/drive_sync.py) e processa
cada uma que ainda não foi concluída com SUCESSO (core/orchestrator.py), sem
intervenção manual - pensado pra rodar sozinho via Tarefa Agendada do Windows.

Cada aula é isolada: se uma falhar, o script loga o erro e continua para as
próximas. No final, imprime um resumo com quantas aulas novas foram encontradas,
quantas deram certo e quantas falharam (com o motivo de cada falha).

Uso manual: venv\\Scripts\\python.exe auto_pipeline.py
"""
import sys

from core.sheets_sync import AVAILABLE_UCS
from core.drive_sync import drive_sync
from core.notebooklm_client import notebooklm_client
from core.orchestrator import orchestrator
from database.db import db_manager
from utils.logger import logger


def run() -> int:
    logger.info("=" * 70)
    logger.info("Iniciando execução automática do pipeline MedStudy Automator")
    logger.info("=" * 70)

    # Checagem prévia: confere se a sessão do NotebookLM CLI está válida DE
    # VERDADE antes de tocar em qualquer aula. Sem isso, uma autenticação
    # expirada só era descoberta no meio do processamento (depois de já ter
    # gasto tempo com outras etapas) - agora falha rápido e visível de cara.
    logger.info("Verificando autenticação do NotebookLM CLI antes de começar...")
    auth_check = notebooklm_client.check_auth()
    if not auth_check["success"]:
        logger.error("=" * 70)
        logger.error("⚠️  ALERTA: Sessão do NotebookLM CLI inválida/expirada - abortando ANTES de")
        logger.error("⚠️  processar qualquer aula (checagem prévia falhou).")
        logger.error(f"⚠️  Detalhe: {auth_check['error']}")
        logger.error("⚠️  Rode 'notebooklm login' manualmente para reautenticar, depois rode")
        logger.error("⚠️  este script de novo.")
        logger.error("=" * 70)
        return 1
    logger.info("Autenticação do NotebookLM CLI OK - prosseguindo com o processamento.")

    new_lessons_found = 0
    succeeded: list = []
    failed: list = []  # list[(nome, motivo)]

    for unit_code in AVAILABLE_UCS:
        lessons = drive_sync.scan_local_lessons(unit_code)
        if not lessons:
            continue

        for lesson in lessons:
            lesson_name = lesson["lesson_title"]

            if db_manager.is_lesson_completed(unit_code, lesson_name):
                logger.debug(f"[{unit_code}] '{lesson_name}' já processada com sucesso - pulando.")
                continue

            # drive_sync.scan_local_lessons só procura slide/áudio direto dentro da
            # pasta da aula (não recursivamente); pastas com estrutura diferente (ex.:
            # subpastas por caso/tópico) aparecem sem nenhum arquivo detectado. Sem
            # slide nem áudio não há nada pra processar - pula sem desperdiçar uma
            # chamada real ao NotebookLM/Gemini, e sem marcar como concluída (fica
            # disponível pra alguém investigar/reorganizar a pasta manualmente).
            if not lesson.get("slide") and not lesson.get("audio"):
                logger.warning(
                    f"[{unit_code}] '{lesson_name}' não tem slide nem áudio direto na pasta "
                    f"({lesson.get('folder_path')}) - pulando (verifique a estrutura da pasta)."
                )
                continue

            new_lessons_found += 1
            logger.info(f"[{unit_code}] Aula nova detectada: '{lesson_name}' - iniciando processamento...")

            try:
                success = orchestrator.process_lesson(
                    unit_code=unit_code,
                    lesson_name=lesson_name,
                    slide_path=lesson.get("slide"),
                    audio_path=lesson.get("audio"),
                )
            except Exception as e:
                logger.error(f"[{unit_code}] Erro NÃO tratado ao processar '{lesson_name}': {e}")
                success = False

            label = f"[{unit_code}] {lesson_name}"
            if success:
                succeeded.append(label)
            else:
                status_row = db_manager.get_lesson_status(unit_code, lesson_name)
                reason = (
                    status_row["details"]
                    if status_row and status_row.get("details")
                    else "motivo não registrado (ver log completo acima)"
                )
                failed.append((label, reason))

    # ------------------------------------------------------------------
    # Resumo final - sempre impresso, mesmo se nada tiver dado errado.
    # ------------------------------------------------------------------
    logger.info("-" * 70)
    logger.info(
        f"Execução automática concluída: {new_lessons_found} aula(s) nova(s) encontrada(s), "
        f"{len(succeeded)} processada(s) com sucesso, {len(failed)} falharam."
    )
    for s in succeeded:
        logger.info(f"  OK: {s}")
    for name, reason in failed:
        logger.error(f"  FALHOU: {name} -> {reason}")

    if failed:
        # Aviso bem visível: se TODAS as falhas da noite foram na criação do
        # NotebookLM, é sintoma clássico de autenticação expirada no CLI - que
        # derruba a noite inteira de uma vez e passa despercebido se ninguém olhar.
        notebook_creation_failures = [1 for _, reason in failed if "criação do NotebookLM" in reason]
        if len(notebook_creation_failures) == len(failed):
            logger.error("=" * 70)
            logger.error("⚠️  ALERTA: TODAS as falhas desta execução foram na CRIAÇÃO do NotebookLM.")
            logger.error("⚠️  Isso é sintoma clássico de AUTENTICAÇÃO EXPIRADA no CLI do notebooklm.")
            logger.error("⚠️  Rode 'notebooklm login' manualmente para reautenticar, depois rode")
            logger.error("⚠️  este script de novo para reprocessar as aulas que falharam.")
            logger.error("=" * 70)

    logger.info("=" * 70)
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(run())
