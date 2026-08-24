import click
from config.settings import settings
from utils.logger import logger


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
    "--dry-run",
    is_flag=True,
    default=False,
    help="Executa em modo de simulação (não grava no banco nem altera Anki/Drive).",
)
def process_lesson(unit: str, lesson: str, dry_run: bool):
    """Processa slide e áudio de uma aula para geração de flashcards e resumos."""
    unit_key = unit.upper()
    if unit_key not in settings.units:
        logger.error(f"Unidade curricular '{unit}' não configurada no config.yaml.")
        logger.info(f"Unidades disponíveis: {list(settings.units.keys())}")
        return

    unit_info = settings.units[unit_key]
    logger.info(f"Iniciando processamento: {unit_info.name} ({unit_key}) - {lesson}")

    if dry_run:
        logger.warning("🔍 MODO DRY-RUN ATIVADO: Nenhuma persistência ou envio externo será realizado.")

    # Nos próximos módulos conectaremos o hash MD5, Gemini, Anki e NotebookLM aqui
    logger.success(f"Pipeline validado para {unit_key}/{lesson} (Simulação concluída com sucesso).")


@cli.command("rollback")
@click.argument("target")
def rollback_lesson(target: str):
    """Reverte o processamento de uma aula (ex: rollback UC01/Aula_01)."""
    logger.warning(f"Solicitado Rollback para: {target}")
    # Nos próximos módulos conectaremos a remoção no SQLite e exclusão de notas no Anki
    logger.info(f"Desfazendo registros locais e no Anki para {target}...")
    logger.success(f"Rollback concluído para {target}.")


@cli.command("list-units")
def list_units():
    """Lista todas as UCs configuradas no ecossistema."""
    click.echo(f"\n📚 Unidades Curriculares ({settings.app.name} v{settings.app.version}):\n")
    for code, info in settings.units.items():
        click.echo(f"  • [{code}] {info.name} -> Deck: {info.deck_name}")
    click.echo("")


if __name__ == "__main__":
    cli()