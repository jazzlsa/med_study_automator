from pathlib import Path
from typing import Dict, List, Optional
import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings

# Carrega variáveis do arquivo .env
load_dotenv()


class AppConfig(BaseModel):
    name: str = "MedStudyAutomator"
    version: str = "1.0.0"
    log_level: str = "INFO"
    anki_root_deck: str = "Medicina"


class DatabaseConfig(BaseModel):
    path: Path = Path("database/lessons.db")


class StorageConfig(BaseModel):
    raw_dir: Path = Path("data/raw")
    processed_dir: Path = Path("data/processed")
    temp_dir: Path = Path("data/temp")
    exports_dir: Path = Path("data/exports")


class UnitInfo(BaseModel):
    name: str
    deck_name: str


class FlashcardRules(BaseModel):
    min_cards_per_hour: int = 8
    max_cards_per_hour: int = 25
    extract_slide_images: bool = True
    allowed_tags: List[str] = Field(default_factory=list)


class EnvSecrets(BaseSettings):
    GEMINI_API_KEY: Optional[str] = None
    ANKI_CONNECT_URL: str = "http://localhost:8765"
    GOOGLE_CREDENTIALS_PATH: Optional[Path] = None
    GOOGLE_SPREADSHEET_ID: Optional[str] = None
    WHATSAPP_API_TOKEN: Optional[str] = None

    class Config:
        env_file = ".env"
        extra = "ignore"


class AppSettings(BaseModel):
    app: AppConfig = Field(default_factory=AppConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    units: Dict[str, UnitInfo] = Field(default_factory=dict)
    flashcards: FlashcardRules = Field(default_factory=FlashcardRules)
    secrets: EnvSecrets = Field(default_factory=EnvSecrets)


def load_settings(config_yaml_path: str = "config/config.yaml") -> AppSettings:
    """Carrega as configurações do YAML e variáveis de ambiente."""
    yaml_path = Path(config_yaml_path)
    if not yaml_path.exists():
        raise FileNotFoundError(f"Arquivo de configuração não encontrado: {config_yaml_path}")

    with open(yaml_path, "r", encoding="utf-8") as f:
        yaml_data = yaml.safe_load(f) or {}

    secrets = EnvSecrets()
    settings = AppSettings(**yaml_data, secrets=secrets)
    return settings


# Instância global reutilizável
settings = load_settings()