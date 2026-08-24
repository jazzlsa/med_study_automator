import sys
from pathlib import Path
from loguru import logger
from config.settings import settings

# Garante que o diretório de logs exista
LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)

# Remove a configuração padrão do loguru
logger.remove()

# 1. Log formatado no terminal (colorido)
logger.add(
    sys.stdout,
    colorize=True,
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{module}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
    level=settings.app.log_level,
)

# 2. Log persistente em arquivo com rotação diária
logger.add(
    LOG_DIR / "app_{time:YYYY-MM-DD}.log",
    rotation="1 day",
    retention="14 days",
    compression="zip",
    format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {module}:{line} - {message}",
    level="DEBUG",
    encoding="utf-8",
)

__all__ = ["logger"]