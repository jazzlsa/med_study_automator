"""Sincroniza pro Anki local (via AnkiConnect) os flashcards de aulas que já
foram processadas com sucesso pelo pipeline na nuvem (Cloud Run Job), mas
ainda não tiveram seus flashcards importados no Anki desta máquina.

Por quê isso existe: o Cloud Run não alcança o AnkiConnect (é um servidor
HTTP em localhost:8765 na máquina da usuária) - core/anki_connect.py já
documenta isso. Toda aula processada na nuvem gera e publica um .apkg no
Drive normalmente (core/anki_flashcards.py + core/drive_sync.py), mas a
importação "ao vivo" fica pendente até uma máquina com o Anki aberto rodar
este script.

NÃO reprocessa nada (não toca em Gemini/NotebookLM/Drive) - só importa,
via AnkiConnect ('importPackage'), o .apkg que a aula já gerou. A mídia
(imagens de slide) já vem embutida no .apkg, então isso funciona mesmo pra
aulas processadas há dias, muito depois do container do Cloud Run daquela
execução ter sido destruído.

Lê e atualiza o MESMO banco (GCS) que o Cloud Run usa (config/credentials.json
precisa ter sido dado acesso de leitura/escrita nesse bucket - ver
database/db.py), marcando cada aula sincronizada em `anki_synced_at`. Roda de
novo sem duplicar nada: aulas já marcadas não são tentadas de novo, e mesmo
que fossem, o AnkiConnect já ignora notas duplicadas dentro do mesmo deck.

Se o Anki não estiver aberto (ou o addon AnkiConnect não estiver instalado),
sai silenciosamente sem erro - esse é o caso normal quando ninguém está
usando o computador. Pensado pra rodar sozinho via Tarefa Agendada do
Windows, de tempos em tempos (ex.: a cada 30 min), sem precisar do Anki
aberto o tempo todo pra funcionar - só sincroniza quando encontra o Anki
aberto numa dessas rodadas.

Uso manual: venv\\Scripts\\python.exe sync_cloud_flashcards_to_anki.py
"""
import os
import sys

# Aponta pro MESMO bucket que o Cloud Run usa, mas num arquivo local
# SEPARADO (nunca o data/med_automator.db "normal" desta máquina) - sem
# isso, baixar o banco da nuvem aqui sobrescreveria qualquer registro de
# processamento puramente local (ex.: uma aula rodada manualmente nesta
# máquina que ainda não subiu pra nuvem), perdendo dado à toa.
os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", "config/credentials.json")
os.environ.setdefault("GCS_DB_BUCKET", "gen-lang-client-0055758379-medstudy-db")
os.environ.setdefault("GCS_DB_BLOB", "med_automator.db")

from core import anki_connect
from core.drive_sync import DriveFolderScanner
from core.orchestrator import _safe_filename
from database.db import DatabaseManager
from utils.logger import logger


def run() -> int:
    if not anki_connect.is_available():
        logger.info("Anki/AnkiConnect não está acessível agora (Anki fechado?) - nada a fazer, saindo.")
        return 0

    logger.info("Anki detectado e acessível - baixando o banco de dados compartilhado (GCS) pra conferir pendências...")
    db = DatabaseManager(db_path="data/cloud_sync_cache.db")
    pending = db.get_lessons_pending_anki_sync()

    if not pending:
        logger.info("Nenhuma aula pendente de sincronização com o Anki.")
        return 0

    logger.info(f"{len(pending)} aula(s) concluída(s) na nuvem, pendente(s) de importar no Anki local...")

    scanner = DriveFolderScanner()
    imported = 0
    missing = 0
    failed = 0

    for lesson in pending:
        unit_code = lesson["unit_code"]
        lesson_name = lesson["lesson_name"]
        apkg_path = scanner.resolve_apkg_output_path(unit_code, _safe_filename(lesson_name))

        if not apkg_path.exists():
            logger.warning(
                f"[{unit_code}] '{lesson_name}': .apkg esperado não encontrado em '{apkg_path}' "
                f"(ainda sincronizando do Drive pro Google Drive Desktop? verifique de novo daqui a "
                f"pouco) - pulando por enquanto, sem marcar como sincronizada."
            )
            missing += 1
            continue

        result = anki_connect.import_apkg_package(apkg_path)
        if result["success"] and result["available"]:
            db.mark_anki_synced(unit_code, lesson_name)
            imported += 1
        elif not result["available"]:
            # Anki fechou entre o check inicial e agora - improvável mas possível;
            # simplesmente para por aqui, tenta o resto na próxima execução.
            logger.warning("Anki parou de responder no meio da sincronização - parando por aqui, tenta de novo na próxima execução.")
            break
        else:
            failed += 1

    logger.info(
        f"Sincronização com o Anki concluída: {imported} importada(s), "
        f"{missing} .apkg ainda não encontrado(s), {failed} falha(s)."
    )
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(run())
