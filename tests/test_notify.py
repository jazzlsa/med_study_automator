"""Testes do módulo de notificação (utils/notify.py).

O contrato mais importante é o no-op: sem NTFY_TOPIC configurada, o pipeline tem
que se comportar exatamente como antes - sem tentar rede e sem lançar exceção.
"""
from config.settings import settings
from utils.notify import send_notification, is_configured


def test_sem_topic_nao_notifica(monkeypatch):
    monkeypatch.setattr(settings.secrets, "NTFY_TOPIC", None)
    assert is_configured() is False
    # No-op seguro: não lança e retorna False (nada foi enviado).
    assert send_notification("Título", "mensagem", priority="urgent") is False


def test_com_topic_configurado(monkeypatch):
    monkeypatch.setattr(settings.secrets, "NTFY_TOPIC", "teste-meucanal")
    assert is_configured() is True
