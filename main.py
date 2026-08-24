from pathlib import Path
import click
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from core.orchestrator import orchestrator
from database.db import db_manager

console = Console()


@click.group()
def cli():
    """MedStudy Automator - Processador de Aulas e Flashcards Médicos com IA."""
    pass


@cli.command("process")
@click.option("--unit", "-u", required=True, help="Código ou nome da Unidade Curricular (ex: UC01, Nefrologia).")
@click.option("--lesson", "-l", required=True, help="Título formal da aula (ex: 'Sindrome Nefritica').")
@click.option("--slide", "-s", type=click.Path(exists=True), default=None, help="Caminho do arquivo de slide (PDF).")
@click.option("--audio", "-a", type=click.Path(exists=True), default=None, help="Caminho da gravação de áudio (MP3/M4A/WAV).")
@click.option("--force", is_flag=True, default=False, help="Força reprocessamento ignorando cache de hash.")
@click.option("--no-sync", is_flag=True, default=False, help="Desativa o envio automático para o Anki via AnkiConnect.")
def process_command(unit, lesson, slide, audio, force, no_sync):
    """Processa slides/áudios e gera baralhos Anki completos."""
    console.print(Panel.fit(f"[bold cyan]Processando Aula:[/bold cyan] [yellow]{lesson}[/yellow] ([green]{unit}[/green])", border_style="cyan"))

    res = orchestrator.process_lesson(
        unit_code=unit,
        lesson_name=lesson,
        slide_path=Path(slide) if slide else None,
        audio_path=Path(audio) if audio else None,
        force_reprocess=force,
        sync_anki=not no_sync,
    )

    if res["status"] == "success":
        console.print(f"\n[bold green] Sucesso![/bold green] Baralho salvo em: [underline]{res['apkg_path']}[/underline]")
        console.print(f"Total de Flashcards: [bold magenta]{res['cards_count']}[/bold magenta] | Tempo: [bold blue]{res['execution_time']:.2f}s[/bold blue]\n")
    elif res["status"] == "skipped":
        console.print(f"\n[bold yellow]⏭️ Aula ignorada (já processada).[/bold yellow] Arquivo existente: {res['apkg_path']}")


@cli.command("stats")
def stats_command():
    """Exibe estatísticas agregadas de execução, tokens e cards gerados."""
    stats = db_manager.get_total_stats()

    table = Table(title=" Estatísticas de Processamento - MedStudy Automator", border_style="blue")
    table.add_column("Métrica", style="cyan", justify="left")
    table.add_column("Valor Total", style="magenta", justify="right")

    table.add_row("Total de Aulas Processadas", str(stats["total_lessons"]))
    table.add_row("Total de Flashcards Gerados", str(stats["total_cards"]))
    table.add_row("Tokens de Prompt Consumidos", f"{stats['total_prompt_tokens']:,}")
    table.add_row("Tokens de Resposta Gerados", f"{stats['total_completion_tokens']:,}")
    table.add_row("Tempo Total de Execução", f"{stats['total_execution_time']:.2f} segundos")

    console.print(table)


@cli.command("list")
def list_command():
    """Lista todas as aulas já salvas no banco de dados local."""
    import sqlite3

    conn = sqlite3.connect(db_manager.db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT id, unit_code, lesson_name, cards_count, created_at, apkg_path FROM lessons ORDER BY id DESC")
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        console.print("[yellow]Nenhuma aula registrada até o momento.[/yellow]")
        return

    table = Table(title=" Aulas Registradas", border_style="green")
    table.add_column("ID", style="dim", width=4)
    table.add_column("Unidade", style="cyan")
    table.add_column("Aula", style="bold white")
    table.add_column("Cards", justify="right", style="magenta")
    table.add_column("Data", style="blue")
    table.add_column("Arquivo APKG", style="dim")

    for r in rows:
        table.add_row(str(r[0]), str(r[1]), str(r[2]), str(r[3]), str(r[4])[:19], Path(str(r[5])).name if r[5] else "-")

    console.print(table)


if __name__ == "__main__":
    cli()