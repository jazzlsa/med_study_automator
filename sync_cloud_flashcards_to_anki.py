"""Importa pro Anki local (via AnkiConnect) os .apkg que já foram gerados pelo
pipeline e sincronizados pra pasta local do Google Drive Desktop.

MECANISMO (opção B - sem GCS):
Antes, a "pendência" era lida de um banco compartilhado no GCS (resquício do
Cloud Run) - que o pipeline deixou de alimentar quando migrou pro Raspberry Pi,
então o AnkiSync parou de enxergar as aulas novas. Agora quem decide "o que
espera pra importar" é observável e simples: os arquivos `.apkg` que já chegaram
na pasta local do Drive Desktop (G:\\Meu Drive\\MedStudy_Flashcards\\...), baixada
pelo próprio Google Drive Desktop, e que ainda não foram importados.

- NÃO reprocessa nada (não toca em Gemini/NotebookLM/Drive): só importa via
  AnkiConnect ('importPackage') o .apkg que ainda não foi importado.
- A mídia (imagens de slide) já vem embutida no .apkg, então funciona mesmo pra
  aulas processadas há dias.
- O "já importado" fica num registro LOCAL (data/anki_imported_registry.json),
  sem depender de banco na nuvem. Na primeira execução, o registro é SEMEADO a
  partir do histórico de anki_synced_at do banco de cache local antigo, pra não
  reimportar o que já foi importado na era GCS.
- Se o Anki não estiver aberto (ou o addon AnkiConnect não estiver instalado),
  sai silenciosamente sem erro - caso normal quando ninguém está usando a
  máquina. Pensado pra rodar sozinho via Tarefa Agendada do Windows, de tempos
  em tempos (ex.: a cada 30 min): só sincroniza quando encontra o Anki aberto.

Uso manual: venv\\Scripts\\python.exe sync_cloud_flashcards_to_anki.py
"""
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from config.settings import settings
from core import anki_connect
from core.orchestrator import _safe_filename
from utils.logger import logger

# Mesma localização usada pelo backend local (core/drive_sync.py) para publicar
# os .apkg: pasta do Google Drive Desktop + nome da pasta de flashcards do semester.
def _flashcards_root() -> Path:
    return Path(r"G:\Meu Drive") / settings.semester.drive_flashcards_folder_name

# Registro local de "já importado": mapeia caminho absoluto do .apkg -> timestamp ISO.
# Fica em data/ (gitignored) - é estado operacional desta máquina, não do projeto.
_REGISTRY_PATH = Path("data/anki_imported_registry.json")
# Banco de cache local da ERA GCS - usado UMA vez, na primeira execução, só pra
# semear o registro e não reimportar o que já foi importado antigamente.
_LEGACY_CACHE_DB = Path("data/cloud_sync_cache.db")


def _load_registry() -> dict:
    if _REGISTRY_PATH.exists():
        try:
            data = json.loads(_REGISTRY_PATH.read_text("utf-8"))
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Registro de importação ilegível ({_REGISTRY_PATH}) - recomeçando: {e}")
    return {}


def _save_registry(registry: dict) -> None:
    try:
        _REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
        _REGISTRY_PATH.write_text(
            json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError as e:
        logger.error(f"Não consegui salvar o registro de importação ({_REGISTRY_PATH}): {e}")


def _seed_registry_from_legacy(registry: dict) -> None:
    """Primeira execução: marca como "já importado" tudo que o fluxo antigo (GCS)
    já tinha sincronizado (anki_synced_at preenchido), pra não duplicar no Anki
    quando o registro novo entra em vigor. Caminho esperado = mesmo padrão de
    nome de arquivo usado na publicação."""
    if _REGISTRY_PATH.exists():  # registro novo já existe - não re-semear
        return
    if not _LEGACY_CACHE_DB.exists():
        return
    root = _flashcards_root()
    try:
        con = sqlite3.connect(str(_LEGACY_CACHE_DB))
        rows = con.execute(
            "SELECT unit_code, lesson_name FROM completed_lessons "
            "WHERE anki_synced_at IS NOT NULL"
        ).fetchall()
        con.close()
    except sqlite3.Error as e:
        logger.warning(f"Não consegui ler o histórico antigo pra semear o registro: {e}")
        return

    if not rows:
        return
    seeded = 0
    for unit_code, lesson_name in rows:
        expected = root / unit_code / f"{_safe_filename(lesson_name)}.apkg"
        registry[str(expected)] = _now_iso()
        seeded += 1
    _save_registry(registry)
    logger.info(
        f"Registro de importação semeado a partir do histórico antigo: {seeded} aula(s) "
        f"já tratada(s) como importadas (não serão reimportadas)."
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def run() -> int:
    if not _REGISTRY_PATH.parent.exists():
        _REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)

    registry = _load_registry()
    _seed_registry_from_legacy(registry)

    root = _flashcards_root()
    if not root.exists():
        logger.info(
            f"Pasta do Drive Desktop não encontrada em '{root}' (Drive Desktop desligado ou "
            f"não sincronizado?) - nada a fazer, saindo."
        )
        return 0

    if not anki_connect.is_available():
        logger.info("Anki/AnkiConnect não está acessível agora (Anki fechado?) - nada a fazer, saindo.")
        return 0

    apkg_files = sorted(root.rglob("*.apkg"))
    if not apkg_files:
        logger.info(f"Nenhum .apkg encontrado em '{root}'.")
        return 0

    pending = [p for p in apkg_files if str(p) not in registry]
    if not pending:
        logger.info(f"Todos os {len(apkg_files)} .apkg já importados - nada a fazer.")
        return 0

    logger.info(
        f"{len(pending)} .apkg novo(s) pra importar (de {len(apkg_files)} no total) - sincronizando..."
    )

    imported = 0
    failed = 0
    for apkg_path in pending:
        result = anki_connect.import_apkg_package(apkg_path)
        if result["success"] and result["available"]:
            registry[str(apkg_path)] = _now_iso()
            imported += 1
        elif not result["available"]:
            # Anki fechou entre o check inicial e agora - improvável mas possível;
            # para por aqui e tenta o resto na próxima execução (sem marcar nada).
            logger.warning("Anki parou de responder no meio da sincronização - parando, retoma na próxima execução.")
            _save_registry(registry)  # preserva o progresso até aqui
            return 0
        else:
            failed += 1
            logger.warning(f"Falha ao importar '{apkg_path.name}': {result['error']}")

    _save_registry(registry)
    logger.info(
        f"Importação concluída: {imported} importado(s), {failed} com falha de AnkiConnect "
        f"(não marcados - tentativa na próxima execução)."
    )
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(run())
