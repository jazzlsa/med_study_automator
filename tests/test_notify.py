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


def test_emoji_no_titulo_nao_quebra_header(monkeypatch):
    """Header HTTP vai como latin-1; emoji no título não pode estourar o POST
    (bug real pego em produção: 'latin-1 codec can't encode character'\U0001f6a8)."""

    class FakeResp:
        def raise_for_status(self):
            return None

    captured = {}

    def fake_post(url, *, data, headers, timeout):
        captured["headers"] = headers
        return FakeResp()

    monkeypatch.setattr(settings.secrets, "NTFY_TOPIC", "teste-meucanal")
    monkeypatch.setattr("utils.notify.requests.post", fake_post)

    ok = send_notification("🚨 Título com acentos ção", "corpo", tags=["rotating_light"])
    assert ok is True
    # o header resultante tem que ser serializável em latin-1 (não estoura)
    captured["headers"]["Title"].encode("latin-1")
    assert "Título" in captured["headers"]["Title"]
    assert "🚨" not in captured["headers"]["Title"]  # emoji fora, tag de ícone entra
    assert captured["headers"]["Tags"] == "rotating_light"


def test_emoji_no_inicio_nao_deixa_espaco_no_header(monkeypatch):
    """Remover o emoji do início do título deixa um espaço residual ('🚨 Aula' vira
    ' Aula'), e esse espaço na ponta do valor de um header faz o http.client do
    Python 3.13+ rejeitar o POST ('Invalid leading whitespace...') - bug real que
    derrubou a notificação de sucesso de 'Aula 8' no Raspberry Pi (12:19 em
    2026-08-31). O valor precisa sair SEM espaço nas pontas."""

    class FakeResp:
        def raise_for_status(self):
            return None

    captured = {}

    def fake_post(url, *, data, headers, timeout):
        captured["headers"] = headers
        return FakeResp()

    monkeypatch.setattr(settings.secrets, "NTFY_TOPIC", "teste-meucanal")
    monkeypatch.setattr("utils.notify.requests.post", fake_post)

    ok = send_notification("✅ Aula concluída: Aula 8", "corpo", tags=["white_check_mark"])
    assert ok is True
    title = captured["headers"]["Title"]
    assert title == title.strip()  # sem espaço nas pontas (pega o bug do http.client)
    assert not title.startswith(" ") and not title.endswith(" ")
    assert title == "Aula concluída: Aula 8"  # exatamente o título, sem resto de emoji
    assert captured["headers"]["Tags"] == "white_check_mark"
