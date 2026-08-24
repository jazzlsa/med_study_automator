import sqlite3
from pathlib import Path
from typing import Dict, Any
from utils.logger import logger

class DatabaseManager:
    """Gerencia a conexão com o banco de dados SQLite para registro das aulas e estatísticas."""

    def __init__(self, db_path: str = "data/medstudy.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        """Cria as tabelas necessárias caso não existam."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS lessons (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    unit_code TEXT NOT NULL,
                    lesson_name TEXT NOT NULL,
                    summary TEXT,
                    cards_count INTEGER DEFAULT 0,
                    apkg_path TEXT,
                    execution_time REAL,
                    prompt_tokens INTEGER DEFAULT 0,
                    completion_tokens INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Erro ao inicializar banco de dados: {e}")

    def save_lesson_record(
        self,
        unit_code: str,
        lesson_name: str,
        summary: str,
        cards_count: int,
        apkg_path: str,
        execution_time: float,
        prompt_tokens: int = 0,
        completion_tokens: int = 0
    ) -> int:
        """Salva ou atualiza o registro de uma aula processada no banco local."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Verifica se já existe registro para esta aula para atualizar ou inserir novo
            cursor.execute(
                "SELECT id FROM lessons WHERE unit_code = ? AND lesson_name = ?",
                (unit_code, lesson_name)
            )
            row = cursor.fetchone()

            if row:
                lesson_id = row[0]
                cursor.execute('''
                    UPDATE lessons 
                    SET summary = ?, cards_count = ?, apkg_path = ?, execution_time = ?, prompt_tokens = ?, completion_tokens = ?
                    WHERE id = ?
                ''', (summary, cards_count, apkg_path, execution_time, prompt_tokens, completion_tokens, lesson_id))
            else:
                cursor.execute('''
                    INSERT INTO lessons (unit_code, lesson_name, summary, cards_count, apkg_path, execution_time, prompt_tokens, completion_tokens)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (unit_code, lesson_name, summary, cards_count, apkg_path, execution_time, prompt_tokens, completion_tokens))
                lesson_id = cursor.lastrowid

            conn.commit()
            conn.close()
            return lesson_id
        except Exception as e:
            logger.error(f"Erro ao salvar registro da aula no banco: {e}")
            return 0

    def get_total_stats(self) -> Dict[str, Any]:
        """Retorna as estatísticas totais para o dashboard na barra lateral."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute("SELECT COUNT(*), SUM(cards_count), SUM(prompt_tokens), SUM(completion_tokens) FROM lessons")
            row = cursor.fetchone()
            conn.close()

            if row and row[0] is not None:
                return {
                    "total_lessons": row[0],
                    "total_cards": row[1] or 0,
                    "total_prompt_tokens": row[2] or 0,
                    "total_completion_tokens": row[3] or 0
                }
        except Exception as e:
            logger.error(f"Erro ao buscar estatísticas: {e}")

        return {
            "total_lessons": 0,
            "total_cards": 0,
            "total_prompt_tokens": 0,
            "total_completion_tokens": 0
        }

db_manager = DatabaseManager()