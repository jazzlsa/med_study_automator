"""Testes do stop por orçamento no auto_pipeline.py.

O contrato: com a cota diária gratuita do Gemini esgotada, o pipeline aborta
ANTES de varrer qualquer UC (em vez de começar a noite e cada aula falhar na
transcrição). Sem estourar, prossegue normalmente.
"""
import auto_pipeline
from core.multimodal_processor import GEMINI_FREE_TIER_DAILY_LIMIT


def _mock_auth_ok(monkeypatch):
    monkeypatch.setattr(auto_pipeline.notebooklm_client, "check_auth", lambda: {"success": True})


def test_cota_esgotada_aborta_antes_de_varrer(monkeypatch):
    _mock_auth_ok(monkeypatch)
    monkeypatch.setattr(
        auto_pipeline.db_manager, "get_gemini_request_count_today",
        lambda: GEMINI_FREE_TIER_DAILY_LIMIT,  # já no limite
    )
    scanned = {"ucs": 0}
    monkeypatch.setattr(
        auto_pipeline.drive_sync, "scan_local_lessons",
        lambda unit_code: scanned.__setitem__("ucs", scanned["ucs"] + 1) or [],
    )

    assert auto_pipeline.run() == 1
    # Nenhuma UC foi nem varrida - abortou antes de qualquer processamento.
    assert scanned["ucs"] == 0


def test_com_folga_prossegue_sem_aulas(monkeypatch):
    _mock_auth_ok(monkeypatch)
    monkeypatch.setattr(auto_pipeline.db_manager, "get_gemini_request_count_today", lambda: 0)
    monkeypatch.setattr(auto_pipeline.drive_sync, "scan_local_lessons", lambda unit_code: [])

    assert auto_pipeline.run() == 0
