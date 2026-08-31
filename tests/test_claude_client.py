import json
from pathlib import Path
from core.claude_client import ClaudeClient


def test_claude_client_parse_json_valid():
    """Testa parsing de JSON padrão e markdown code block."""
    raw = '```json\n{"flashcards": [{"tipo": "mc", "enunciado": "Pergunta?"}]}\n```'
    parsed = ClaudeClient._parse_json_response(raw)
    assert "flashcards" in parsed
    assert len(parsed["flashcards"]) == 1
    assert parsed["flashcards"][0]["tipo"] == "mc"


def test_claude_client_parse_json_repaired():
    """Testa recuperação com json_repair quando há aspas não escapadas."""
    raw = '{"flashcards": [{"tipo": "mc", "enunciado": "O professor disse "Atenção!" na aula."}]}'
    parsed = ClaudeClient._parse_json_response(raw)
    assert "flashcards" in parsed
    assert len(parsed["flashcards"]) == 1


def test_claude_client_build_prompt():
    """Testa construção do prompt contendo transcrição e diretrizes médicas."""
    client = ClaudeClient()
    prompt = client._build_prompt(
        lesson_name="Aula 1",
        unit_code="UC16",
        transcript="Transcrição teste de aula sobre vias aéreas.",
        slide_paths=[],
        min_cards=10,
    )
    assert "UC16" in prompt
    assert "Aula 1" in prompt
    assert "Transcrição teste de aula sobre vias aéreas." in prompt
    assert "GABARITO COMENTADO" in prompt
    assert "imagem_slide_pagina" in prompt
