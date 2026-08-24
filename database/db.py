import sqlite3
from pathlib import Path
from typing import Optional, Dict, Any
from config.settings import settings
from utils.logger import logger


class DatabaseManager:
    """Gerencia a persistência em SQLite, controle de idempotência e métricas."""

    def __init__(self, db_path: Optional[Path] = None):
        if db_path:
            self.db_path = Path(db_path)
        elif hasattr(settings.storage, "db_path"):
            self.db_path = settings.storage.db_path
        else:
            self.db_path = Path("data/med_study.db")

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS lessons (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    unit_code TEXT NOT NULL,
                    lesson_name TEXT NOT NULL,
                    content_hash TEXT UNIQUE NOT NULL,
                    cards_count INTEGER DEFAULT 0,
                    apkg_path TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS execution_metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    lesson_id INTEGER,
                    prompt_tokens INTEGER DEFAULT 0,
                    completion_tokens INTEGER DEFAULT 0,
                    execution_time_sec REAL DEFAULT 0.0,
                    cost_usd REAL DEFAULT 0.0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (lesson_id) REFERENCES lessons (id) ON DELETE CASCADE
                );
                """
            )
            conn.commit()

    def get_lesson_by_hash(self, content_hash: str) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM lessons WHERE content_hash = ?", (content_hash,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def save_lesson(
        self,
        unit_code: str,
        lesson_name: str,
        content_hash: str,
        cards_count: int,
        apkg_path: Optional[str] = None,
    ) -> int:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO lessons (unit_code, lesson_name, content_hash, cards_count, apkg_path)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(content_hash) DO UPDATE SET
                    cards_count = excluded.cards_count,
                    apkg_path = excluded.apkg_path;
                """,
                (unit_code, lesson_name, content_hash, cards_count, apkg_path),
            )
            conn.commit()
            return cursor.lastrowid

    def log_metrics(
        self,
        lesson_id: int,
        prompt_tokens: int,
        completion_tokens: int,
        execution_time_sec: float,
        cost_usd: float = 0.0,
    ):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO execution_metrics (lesson_id, prompt_tokens, completion_tokens, execution_time_sec, cost_usd)
                VALUES (?, ?, ?, ?, ?)
                """,
                (lesson_id, prompt_tokens, completion_tokens, execution_time_sec, cost_usd),
            )
            conn.commit()

    def delete_lesson(self, unit_code: str, lesson_name: str) -> bool:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM lessons WHERE unit_code = ? AND lesson_name = ?",
                (unit_code, lesson_name),
            )
            conn.commit()
            return cursor.rowcount > 0

    def get_total_stats(self) -> Dict[str, Any]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*), COALESCE(SUM(cards_count), 0) FROM lessons")
            total_lessons, total_cards = cursor.fetchone()

            cursor.execute(
                """
                SELECT 
                    COALESCE(SUM(prompt_tokens), 0),
                    COALESCE(SUM(completion_tokens), 0),
                    COALESCE(SUM(execution_time_sec), 0.0),
                    COALESCE(SUM(cost_usd), 0.0)
                FROM execution_metrics
                """
            )
            p_tok, c_tok, exec_time, cost = cursor.fetchone()

            return {
                "total_lessons": total_lessons,
                "total_cards": total_cards,
                "total_prompt_tokens": p_tok,
                "total_completion_tokens": c_tok,
                "total_execution_time": exec_time,
                "total_cost_usd": cost,
            }


db_manager = DatabaseManager()