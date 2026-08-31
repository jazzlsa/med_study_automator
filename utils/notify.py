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
        "Title": title,
        "Priority": priority,
    }
    if tags:
        # ntfy aceita tags separadas por vírgula no header (emojis ou códigos).
        headers["Tags"] = ",".join(tags)

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
