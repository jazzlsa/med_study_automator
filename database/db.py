import sqlite3
from pathlib import Path
from typing import List, Dict, Any
from utils.logger import logger

class DatabaseManager:
    """Gerencia o banco de dados SQLite local para controle de aulas processadas."""

    def __init__(self, db_path: str = "data/med_automator.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        """Cria a tabela de controle de aulas se ela não existir."""
        try:
            with self._get_connection() as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS completed_lessons (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        unit_code TEXT NOT NULL,
                        lesson_name TEXT NOT NULL,
                        notebook_id TEXT,
                        processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(unit_code, lesson_name)
                    )
                """)
                conn.commit()
        except Exception as e:
            logger.error(f"Erro ao inicializar o banco de dados: {e}")

    def mark_lesson_completed(self, unit_code: str, lesson_name: str, notebook_id: str):
        """Registra uma aula como processada e salva o ID do NotebookLM."""
        try:
            with self._get_connection() as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO completed_lessons (unit_code, lesson_name, notebook_id)
                    VALUES (?, ?, ?)
                """, (unit_code, lesson_name, notebook_id))
                conn.commit()
            logger.info(f"Aula {lesson_name} salva no banco de dados local.")
        except Exception as e:
            logger.error(f"Erro ao salvar aula no banco de dados: {e}")

    def get_completed_lessons(self, unit_code: str) -> List[Dict[str, Any]]:
        """Retorna a lista de aulas já processadas para uma unidade curricular."""
        lessons = []
        try:
            with self._get_connection() as conn:
                cursor = conn.execute("""
                    SELECT lesson_name, notebook_id, processed_at 
                    FROM completed_lessons 
                    WHERE unit_code = ?
                    ORDER BY processed_at DESC
                """, (unit_code,))
                rows = cursor.fetchall()
                for row in rows:
                    lessons.append({
                        "lesson_name": row["lesson_name"],
                        "notebook_id": row["notebook_id"],
                        "processed_at": row["processed_at"]
                    })
        except Exception as e:
            logger.error(f"Erro ao buscar aulas concluídas: {e}")
        return lessons

db_manager = DatabaseManager()