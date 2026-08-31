"""Testes do nome de deck do Anki (core/anki_connect.py).

O deck é "<root>::<ano>::<UC>::<aula>" construído a partir da config (config.yaml
> app: anki_root_deck + anki_ano_deck) - NÃO hardcoded. Estes testes garantem que
mexer na config muda o deck, e que a formatação é a mesma usada pelo .apkg.
"""
from config.settings import settings
from core.anki_connect import _resolve_deck_name


def test_deck_reflete_root_e_ano_da_config(monkeypatch):
    monkeypatch.setattr(settings.app, "anki_root_deck", "Faculdade")
    monkeypatch.setattr(settings.app, "anki_ano_deck", "2º Ano")
    assert _resolve_deck_name("UC16", "Aula 6") == "Faculdade::2º Ano::UC16::Aula 6"


def test_deck_usa_formatacao_da_config(monkeypatch):
    # Formato genérico <root>::<ano>::<UC>::<aula>, sempre derivado da config.
    assert _resolve_deck_name("UC16", "Aula 6") == (
        f"{settings.app.anki_root_deck}::{settings.app.anki_ano_deck}::UC16::Aula 6"
    )
