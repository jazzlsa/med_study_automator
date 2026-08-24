import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from config.settings import settings
from utils.logger import logger


class DatabaseManager:
    """Gerenciador central de persistência SQLite com suporte a rastreamento e métricas."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or settings.database.path
        # Garante que o diretório do banco exista
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        """Inicializa as tabelas de aulas e métricas de execução se não existirem."""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Tabela de Aulas Processadas (Idempotência e Rastreamento)
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS lessons (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    unit_code TEXT NOT NULL,
                    lesson_name TEXT NOT NULL,
                    slide_path TEXT,
                    audio_path TEXT,
                    content_hash TEXT UNIQUE NOT NULL,
                    cards_count INTEGER DEFAULT 0,
                    apkg_path TEXT,
                    drive_apkg_link TEXT,
                    notebooklm_id TEXT,
                    notebooklm_link TEXT,
                    status TEXT DEFAULT 'PROCESSED',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                """
            )

            # Tabela de Métricas de Execução (Observabilidade)
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS execution_metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    lesson_id INTEGER,
                    unit_code TEXT NOT NULL,
                    lesson_name TEXT NOT NULL,
                    prompt_tokens INTEGER DEFAULT 0,
                    completion_tokens INTEGER DEFAULT 0,
                    total_tokens INTEGER DEFAULT 0,
                    duration_seconds REAL DEFAULT 0.0,
                    success INTEGER DEFAULT 1,
                    error_message TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(lesson_id) REFERENCES lessons(id) ON DELETE CASCADE
                );
                """
            )
            conn.commit()
            logger.debug(f"Banco de dados inicializado em: {self.db_path}")

    def get_lesson_by_hash(self, content_hash: str) -> Optional[Dict[str, Any]]:
        """Busca aula pelo hash do conteúdo para idempotência."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM lessons WHERE content_hash = ?", (content_hash,)
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_lesson(self, unit_code: str, lesson_name: str) -> Optional[Dict[str, Any]]:
        """Busca aula por UC e nome da aula."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM lessons WHERE unit_code = ? AND lesson_name = ?",
                (unit_code.upper(), lesson_name),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def save_lesson(
        self,
        unit_code: str,
        lesson_name: str,
        content_hash: str,
        slide_path: str = "",
        audio_path: str = "",
        cards_count: int = 0,
        apkg_path: str = "",
        drive_apkg_link: str = "",
        notebooklm_id: str = "",
        notebooklm_link: str = "",
        status: str = "PROCESSED",
    ) -> int:
        """Insere ou atualiza os dados de uma aula processada."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO lessons (
                    unit_code, lesson_name, slide_path, audio_path,
                    content_hash, cards_count, apkg_path, drive_apkg_link,
                    notebooklm_id, notebooklm_link, status, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(content_hash) DO UPDATE SET
                    unit_code=excluded.unit_code,
                    lesson_name=excluded.lesson_name,
                    slide_path=excluded.slide_path,
                    audio_path=excluded.audio_path,
                    cards_count=excluded.cards_count,
                    apkg_path=excluded.apkg_path,
                    drive_apkg_link=excluded.drive_apkg_link,
                    notebooklm_id=excluded.notebooklm_id,
                    notebooklm_link=excluded.notebooklm_link,
                    status=excluded.status,
                    updated_at=CURRENT_TIMESTAMP;
                """,
                (
                    unit_code.upper(),
                    lesson_name,
                    slide_path,
                    audio_path,
                    content_hash,
                    cards_count,
                    apkg_path,
                    drive_apkg_link,
                    notebooklm_id,
                    notebooklm_link,
                    status,
                ),
            )
            conn.commit()
            return cursor.lastrowid

    def record_metrics(
        self,
        unit_code: str,
        lesson_name: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        duration_seconds: float = 0.0,
        success: bool = True,
        error_message: Optional[str] = None,
        lesson_id: Optional[int] = None,
    ):
        """Registra métricas de observabilidade de uma execução."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            total_tokens = prompt_tokens + completion_tokens
            cursor.execute(
                """
                INSERT INTO execution_metrics (
                    lesson_id, unit_code, lesson_name, prompt_tokens,
                    completion_tokens, total_tokens, duration_seconds,
                    success, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    lesson_id,
                    unit_code.upper(),
                    lesson_name,
                    prompt_tokens,
                    completion_tokens,
                    total_tokens,
                    duration_seconds,
                    1 if success else 0,
                    error_message,
                ),
            )
            conn.commit()

    def delete_lesson(self, unit_code: str, lesson_name: str) -> bool:
        """Remove registro de aula para suporte ao Rollback."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM lessons WHERE unit_code = ? AND lesson_name = ?",
                (unit_code.upper(), lesson_name),
            )
            conn.commit()
            return cursor.rowcount > 0


# Instância global reutilizável
db = DatabaseManager()