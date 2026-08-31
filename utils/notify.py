"""Notificações por push via ntfy.sh.

Pensado pro caso de maior valor do pipeline: o processamento roda sozinho à noite
(no Windows ou no Pi) e, se algo falhar, ninguém olha o log até de manhã - ou
nunca. Com um tópico ntfy configurado (env var NTFY_TOPIC), manda um push pro
celular na hora, sem precisar de conta pra receber (basta instalar o app ntfy e
assinar o mesmo tópico).

Sem NTFY_TOPIC setada, todas as funções aqui são no-ops seguros - não muda o
comportamento de antes nem quebra nada se o serviço estiver fora do ar.
"""
import requests

from utils.logger import logger
from config.settings import settings

_BASE_URL = "https://ntfy.sh"
_TIMEOUT_SECONDS = 10


def _latin1_safe(value: str) -> str:
    """Torna um valor seguro pra usar num header HTTP.

    O http.client do Python serializa headers como latin-1; emoji e outros
    caracteres multibyte (🚨, ⚠️, 🧪...) estouram com 'latin-1 codec can't encode'.
    Remove o que não couber em latin-1 e mantém o resto - acentos do pt-br (é, ç,
    ã... U+0000-U+00FF) passam sem problema. Pros ícones de aviso, seguimos pelo
    header `Tags` do ntfy, que renderiza os próprios ícones no app."""
    return value.encode("latin-1", errors="ignore").decode("latin-1")


def is_configured() -> bool:
    """True se um tópico ntfy estiver configurado (e o alerta deve disparar)."""
    return bool(settings.secrets.NTFY_TOPIC)


def send_notification(
    title: str,
    message: str,
    priority: str = "default",
    tags: list[str] | None = None,
) -> bool:
    """Envia um push ntfy para o tópico configurado. No-op se não configurado.

    priority: "min" | "low" | "default" | "high" | "urgent" (ver ntfy.sh/docs)
    tags: lista de emojis/tags exibidas junto (ex.: ["rotating_light", "warning"]).
    Retorna False se for no-op ou se o envio falhar (nunca lança exceção).
    """
    topic = settings.secrets.NTFY_TOPIC
    if not topic:
        return False

    headers = {
        # Título/headers vão como latin-1 (limitação do http.client) - emoji do
        # título é removido aqui e os ícones de severidade entram pelo `Tags`.
        "Title": _latin1_safe(title),
        "Priority": priority,
    }
    if tags:
        # ntfy aceita tags separadas por vírgula no header (códigos ASCII como
        # "rotating_light" que ele renderiza como ícone no app).
        headers["Tags"] = ",".join(_latin1_safe(t) for t in tags)

    try:
        resp = requests.post(
            f"{_BASE_URL}/{topic}",
            data=message.encode("utf-8"),
            headers=headers,
            timeout=_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        logger.info(f"Notificação push enviada via ntfy: '{title}' (prioridade {priority})")
        return True
    except Exception as e:
        logger.error(f"Falha ao enviar notificação push (ntfy '{topic}'): {e}")
        return False
