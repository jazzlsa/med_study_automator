"""Testes do reparo/parsing de JSON em core/multimodal_processor.py.

_parse_json_response é onde o JSON retornado pelo Gemini (sem response_schema
estruturado) bate primeiro - e quebra com mais frequência, porque o modelo às
vezes esquece de escapar aspas literais dentro de valores string (causa real
vista em produção). Estes testes cobrem o caminho feliz, o reparo via
json_repair e o caso de resposta truncada/irrecuperável.
"""
import json

import pytest

from core.multimodal_processor import MultimodalProcessor


def _parse(text: str):
    return MultimodalProcessor._parse_json_response(text, "test")


def test_parse_json_valido():
    data = _parse('{"front": "Síndrome nefrítica", "back": "Hematúria + HTA"}')
    assert data == {"front": "Síndrome nefrítica", "back": "Hematúria + HTA"}


def test_parse_lista_de_flashcards():
    text = '[{"front": "A", "back": "1"}, {"front": "B", "back": "2"}]'
    data = _parse(text)
    assert isinstance(data, list)
    assert len(data) == 2
    assert data[1]["front"] == "B"


def test_parse_control_char_dentro_de_string():
    # strict=False tolera controle não escapado (comum em transcrições longas).
    data = _parse('{"texto": "linha 1\nlinha 2"}')
    assert data["texto"] == "linha 1\nlinha 2"


def test_repara_aspas_literais_nao_escapadas():
    # O bug real de produção: "Rapidinho. "Este relato..." quebra o json.loads
    # estrito; json_repair deve recuperar sem lançar.
    text = '{"front": "O médico disse "vai dar certo" e sorriu", "back": "ok"}'
    data = _parse(text)
    assert isinstance(data, dict)


def test_repara_json_truncado_recuperavel():
    # Resposta cortada no fim mas estruturalmente suficiente pro json_repair.
    text = '[{"front": "A", "back": "1"}, {"front": "B", "back"'
    data = _parse(text)
    assert isinstance(data, list)


def test_lixo_irrecuperavel_levanta_original():
    # json_repair devolve vazio (falsy) pra lixo puro -> relança o
    # JSONDecodeError original (a resposta provavelmente veio vazia/truncada).
    with pytest.raises(json.JSONDecodeError):
        _parse('...')
