from pathlib import Path
import click
from config.settings import settings
from database.db import db
from utils.hasher import compute_content_hash
from utils.logger import logger
from utils.metrics import MetricsCollector


@click.group()
def cli():
    """MedStudyAutomator - Ecossistema Autônomo de Estudos Médicos."""
    pass


@cli.command("process")
@click.option(
    "--unit",
    "-u",
    required=True,
    help="Identificador da Unidade Curricular (ex: UC01, UC02).",
)
@click.option(
    "--lesson",
    "-l",
    required=True,
    help="Identificador ou título da aula (ex: Aula_01).",
)
@click.option(
    "--slide",
    "-s",
    type=click.Path(exists=True),
    required=False,
    help="Caminho para o arquivo de slide (PDF).",
)
@click.option(
    "--audio",
    "-a",
    type=click.Path(exists=True),
    required=False,
    help="Caminho para o arquivo de áudio gravado (MP3/M4A/WAV).",
)
@click.option(
    "--force",
    "-f",
    is_flag=True,
    default=False,
    help="Força o reprocessamento ignorando o cache de hash MD5.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Executa em modo de simulação sem gravar no banco.",
)
def process_lesson(
    unit: str,
    lesson: str,
    slide: str,
    audio: str,
    force: bool,
    dry_run: bool,
):
    """Processa slide e áudio de uma aula com idempotência e métricas."""
    unit_key = unit.upper()
    if unit_key not in settings.units:
        logger.error(f"Unidade curricular '{unit}' não configurada no config.yaml.")
        logger.info(f"Unidades disponíveis: {list(settings.units.keys())}")
        return

    unit_info = settings.units[unit_key]
    logger.info(f"Iniciando pipeline: {unit_info.name} ({unit_key}) - {lesson}")

    # Fallback para slide de teste se não informado
    if not slide:
        # Se não fornecido slide, usa o próprio config.yaml como mock para teste
        slide = "config/config.yaml"

    # 1. Cálculo do Hash MD5 combinado para Idempotência
    content_hash = compute_content_hash(slide, audio)
    logger.debug(f"Hash do conteúdo gerado: {content_hash}")

    # 2. Verificação de Cache/Processamento Prévio
    existing_lesson = db.get_lesson_by_hash(content_hash)
    if existing_lesson and not force:
        logger.warning(
            f"⚡ [CACHE HIT] Aula '{lesson}' já foi processada anteriormente em {existing_lesson['updated_at']}."
        )
        logger.info("Use a flag --force (-f) se desejar reprocessar do zero.")
        return

    # 3. Execução com rastreamento de métricas
    with MetricsCollector(unit_key, lesson) as metrics:
        if dry_run:
            logger.warning("🔍 MODO DRY-RUN: Nenhuma gravação persistente será feita.")
            metrics.add_tokens(prompt=120, completion=45)
            logger.success(f"Dry-run concluído com sucesso para {unit_key}/{lesson}.")
            return

        # Simulação de tokens consumidos na geração (será conectado ao Gemini no Módulo 3)
        metrics.add_tokens(prompt=850, completion=320)

        # 4. Persistência dos dados no SQLite
        lesson_id = db.save_lesson(
            unit_code=unit_key,
            lesson_name=lesson,
            content_hash=content_hash,
            slide_path=str(slide),
            audio_path=str(audio) if audio else "",
            cards_count=15,
            status="PROCESSED",
        )
        metrics.lesson_id = lesson_id

        logger.success(
            f"✅ Aula '{lesson}' registrada no SQLite com sucesso (ID: {lesson_id})."
        )


@cli.command("rollback")
@click.argument("target")
def rollback_lesson(target: str):
    """Reverte o processamento de uma aula (ex: rollback UC01/Aula_01)."""
    if "/" not in target:
        logger.error("Formato inválido. Use: UCxx/Nome_Da_Aula (ex: UC01/Aula_01)")
        return

    unit_code, lesson_name = target.split("/", 1)
    logger.warning(f"Executando Rollback para: {unit_code.upper()} - {lesson_name}")

    deleted = db.delete_lesson(unit_code, lesson_name)
    if deleted:
        logger.success(f"Registro de '{lesson_name}' removido do banco com sucesso.")
    else:
        logger.warning(f"Nenhum registro encontrado no banco para '{target}'.")


@cli.command("status")
def status():
    """Exibe o resumo das aulas processadas no banco de dados."""
    import sqlite3

    conn = sqlite3.connect(settings.database.path)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, unit_code, lesson_name, cards_count, status, created_at FROM lessons ORDER BY id DESC"
    )
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        click.echo("\nNenhuma aula processada ainda.\n")
        return

    click.echo("\n📊 Status do Banco de Aulas:")
    click.echo("-" * 75)
    click.echo(f"{'ID':<4} | {'UC':<6} | {'AULA':<20} | {'CARDS':<6} | {'STATUS':<10} | {'DATA'}")
    click.echo("-" * 75)
    for r in rows:
        click.echo(f"{r[0]:<4} | {r[1]:<6} | {r[2]:<20} | {r[3]:<6} | {r[4]:<10} | {r[5]}")
    click.echo("-" * 75 + "\n")


@cli.command("list-units")
def list_units():
    """Lista todas as UCs configuradas no ecossistema."""
    click.echo(f"\n📚 Unidades Curriculares ({settings.app.name} v{settings.app.version}):\n")
    for code, info in settings.units.items():
        click.echo(f"  • [{code}] {info.name} -> Deck: {info.deck_name}")
    click.echo("")


if __name__ == "__main__":
    cli()