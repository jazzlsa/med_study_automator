import time
from typing import Optional
from database.db import db
from utils.logger import logger


class MetricsCollector:
    """Coletor de tempo e tokens com gravação automática no SQLite."""

    def __init__(self, unit_code: str, lesson_name: str, lesson_id: Optional[int] = None):
        self.unit_code = unit_code
        self.lesson_name = lesson_name
        self.lesson_id = lesson_id
        self.start_time = 0.0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.duration = 0.0

    def __enter__(self):
        self.start_time = time.time()
        return self

    def add_tokens(self, prompt: int, completion: int):
        """Acumula tokens utilizados nas chamadas ao Gemini."""
        self.prompt_tokens += prompt
        self.completion_tokens += completion

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.duration = round(time.time() - self.start_time, 2)
        success = exc_type is None
        error_msg = str(exc_val) if not success else None

        db.record_metrics(
            unit_code=self.unit_code,
            lesson_name=self.lesson_name,
            prompt_tokens=self.prompt_tokens,
            completion_tokens=self.completion_tokens,
            duration_seconds=self.duration,
            success=success,
            error_message=error_msg,
            lesson_id=self.lesson_id,
        )

        if success:
            logger.info(
                f"⏱️ Execução concluída em {self.duration}s | Tokens: {self.prompt_tokens + self.completion_tokens} "
                f"(Prompt: {self.prompt_tokens}, Resposta: {self.completion_tokens})"
            )
        else:
            logger.error(f"❌ Falha na execução após {self.duration}s: {error_msg}")