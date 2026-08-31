"""Testes do novo fluxo de sync Anki (opção B - registro local, sem GCS).

Foca nas funções puras (registro de "já importado"): o comportamento de rede
(AnkiConnect) já foi validado com uma execução real. O contrato principal é a
iduempotência: o que já está no registro não é reimportado.
"""
import json

import sync_cloud_flashcards_to_anki as mod


def test_registry_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "_REGISTRY_PATH", tmp_path / "reg.json")
    data = {"G:\\x\\a.apkg": "2026-01-01T00:00:00+00:00"}
    mod._save_registry(data)
    assert mod._load_registry() == data


def test_registry_missing_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "_REGISTRY_PATH", tmp_path / "nao_existe.json")
    assert mod._load_registry() == {}


def test_flashcards_root_usa_drive_desktop():
    root = mod._flashcards_root()
    assert str(root).startswith(r"G:\Meu Drive")
    assert root.name  # começa com o nome do semester (ex.: MedStudy_Flashcards)
