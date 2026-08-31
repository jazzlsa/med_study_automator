import json
from pathlib import Path
from typing import Dict, List, Optional
import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator
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


class SemesterConfig(BaseModel):
    """Tudo que muda de semestre pra semestre - planilha, pastas do Drive, UCs
    cursadas. Fica isolado num bloco só pra ficar fácil de editar (manualmente
    no YAML, ou pela aba "⚙️ Configurações" do app.py) sem tocar em código."""
    spreadsheet_id: Optional[str] = None
    drive_lessons_folder_name: str = "MedStudy_Aulas"
    drive_flashcards_folder_name: str = "MedStudy_Flashcards"
    available_ucs: List[str] = Field(default_factory=list)


class FlashcardRules(BaseModel):
    min_cards_per_hour: int = 8
    max_cards_per_hour: int = 25
    extract_slide_images: bool = True
    allowed_tags: List[str] = Field(default_factory=list)


class EnvSecrets(BaseSettings):
    GEMINI_API_KEY: Optional[str] = None
    # Usada só pra geração de flashcards (core/claude_flashcards.py) - a
    # transcrição de áudio continua no Gemini (única API com entendimento
    # nativo de áudio nesse pipeline); a Claude entra depois, com a
    # transcrição (texto) + slide (PDF) como entrada, sem precisar do áudio.
    ANTHROPIC_API_KEY: Optional[str] = None
    ANKI_CONNECT_URL: str = "http://localhost:8765"
    GOOGLE_CREDENTIALS_PATH: Optional[Path] = None
    GOOGLE_SPREADSHEET_ID: Optional[str] = None
    WHATSAPP_API_TOKEN: Optional[str] = None

    # Alerta por push quando o pipeline noturno falha (ntfy.sh). Basta o nome do
    # tópico - instale o app ntfy no celular e assine o mesmo tópico. Vazio = sem
    # notificação (comportamento de antes). Auto-preenchido como $NTFY_TOPIC.
    NTFY_TOPIC: Optional[str] = None

    # Upload do .apkg de flashcards pro Drive via OAuth da conta pessoal (não a
    # service account, que não tem cota própria em "Meu Drive") - ver
    # core/drive_api.py. Gerados uma vez por scripts/setup_drive_oauth.py.
    GOOGLE_DRIVE_OAUTH_CLIENT_ID: Optional[str] = None
    GOOGLE_DRIVE_OAUTH_CLIENT_SECRET: Optional[str] = None
    GOOGLE_DRIVE_OAUTH_REFRESH_TOKEN: Optional[str] = None

    def model_post_init(self, __context) -> None:
        super().model_post_init(__context)
        # Se os segredos OAuth do Drive não foram definidos no .env / env vars,
        # tenta carregar automaticamente de config/drive_oauth_secrets.json
        if not (self.GOOGLE_DRIVE_OAUTH_CLIENT_ID and self.GOOGLE_DRIVE_OAUTH_CLIENT_SECRET and self.GOOGLE_DRIVE_OAUTH_REFRESH_TOKEN):
            json_path = Path("config/drive_oauth_secrets.json")
            if json_path.exists():
                try:
                    with open(json_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if not self.GOOGLE_DRIVE_OAUTH_CLIENT_ID and data.get("client_id"):
                        self.GOOGLE_DRIVE_OAUTH_CLIENT_ID = str(data["client_id"]).strip()
                    if not self.GOOGLE_DRIVE_OAUTH_CLIENT_SECRET and data.get("client_secret"):
                        self.GOOGLE_DRIVE_OAUTH_CLIENT_SECRET = str(data["client_secret"]).strip()
                    if not self.GOOGLE_DRIVE_OAUTH_REFRESH_TOKEN and data.get("refresh_token"):
                        self.GOOGLE_DRIVE_OAUTH_REFRESH_TOKEN = str(data["refresh_token"]).strip()
                except Exception:
                    pass

    class Config:
        env_file = ".env"
        extra = "ignore"

    @field_validator("*", mode="before")
    @classmethod
    def _strip_whitespace(cls, v):
        """Remove espaço/quebra de linha nas pontas de todo valor string.

        Bug real visto em produção: um secret do Google Secret Manager (usado
        via --set-secrets como env var no Cloud Run) foi criado com "\\r\\r\\n"
        grudado no final do valor (efeito colateral de como o valor foi
        gerado/repassado no PowerShell) - o client_id do OAuth ficava então
        "...googleusercontent.com\\r\\r\\n", que o servidor OAuth do Google
        rejeita como 'invalid_client: The OAuth client was not found' (não bate
        com nenhum client_id real). Passava despercebido em teste manual porque
        `.strip()` era aplicado ali sem querer antes de comparar. Isso garante
        que nenhum EnvSecrets carregue lixo de espaço/quebra de linha, venha de
        onde vier (arquivo .env local ou secret montado como env var)."""
        return v.strip() if isinstance(v, str) else v


class AppSettings(BaseModel):
    app: AppConfig = Field(default_factory=AppConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    units: Dict[str, UnitInfo] = Field(default_factory=dict)
    flashcards: FlashcardRules = Field(default_factory=FlashcardRules)
    semester: SemesterConfig = Field(default_factory=SemesterConfig)
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

_CONFIG_YAML_PATH = Path("config/config.yaml")


def save_semester_config(
    spreadsheet_id: str,
    drive_lessons_folder_name: str,
    drive_flashcards_folder_name: str,
    available_ucs: List[str],
) -> None:
    """Grava o bloco `semester:` de volta em config/config.yaml (usado pela aba
    "⚙️ Configurações" do app.py) e já atualiza a instância `settings` em memória,
    pra a mudança valer na mesma sessão do Streamlit sem precisar reiniciar.

    Atenção: reescreve o YAML inteiro via yaml.safe_dump - comentários e a
    formatação original do arquivo não são preservados (limitação do PyYAML puro),
    só a estrutura/dados."""
    with open(_CONFIG_YAML_PATH, "r", encoding="utf-8") as f:
        yaml_data = yaml.safe_load(f) or {}

    yaml_data["semester"] = {
        "spreadsheet_id": spreadsheet_id,
        "drive_lessons_folder_name": drive_lessons_folder_name,
        "drive_flashcards_folder_name": drive_flashcards_folder_name,
        "available_ucs": available_ucs,
    }

    with open(_CONFIG_YAML_PATH, "w", encoding="utf-8") as f:
        yaml.safe_dump(yaml_data, f, allow_unicode=True, sort_keys=False)

    settings.semester = SemesterConfig(**yaml_data["semester"])
    _sync_watch_folder_to_anki_addon(drive_flashcards_folder_name)


# Caminho do addon local do Anki (fora deste repositório) que varre a pasta de
# flashcards e importa os .apkg sozinho - ver medstudy_auto_import/__init__.py.
_ANKI_ADDON_WATCH_FILE = (
    Path.home() / "AppData" / "Roaming" / "Anki2" / "addons21" / "medstudy_auto_import"
    / "user_files" / "watch_folder.txt"
)


def _sync_watch_folder_to_anki_addon(drive_flashcards_folder_name: str) -> None:
    """Grava o caminho completo da pasta de flashcards num arquivo que o addon do
    Anki lê na inicialização - assim salvar a aba de Configurações já atualiza o
    addon também, sem precisar editar `medstudy_auto_import/__init__.py` à mão.
    Best-effort: se o addon não estiver instalado (pasta não existe), não faz nada
    e não trava o salvamento das configurações por causa disso."""
    try:
        if not _ANKI_ADDON_WATCH_FILE.parent.parent.exists():
            return  # addon não instalado nesta máquina - nada a sincronizar
        _ANKI_ADDON_WATCH_FILE.parent.mkdir(parents=True, exist_ok=True)
        watch_path = str(Path(r"G:\Meu Drive") / drive_flashcards_folder_name)
        _ANKI_ADDON_WATCH_FILE.write_text(watch_path, encoding="utf-8")
    except Exception:
        pass  # sincronização é best-effort - nunca deve travar o salvamento das configurações