from core.sheets_client import SheetsClient
from core.notebooklm_client import NotebookLMClient


def test_parte_1_entra_antes_da_parte_2():
    col = ["Aula", "Aula 8", "Aula 9 - Tema", "Aula 10 - Parte 2 - Caso Clínico 1", "Aula 10 - Parte 3 - Caso Clínico 2"]
    assert SheetsClient._ordered_insert_row(col, "Aula 10 - Parte 1") == 4


def test_aula_sem_parte_vai_pro_final():
    col = ["Aula", "Aula 8", "Aula 9"]
    assert SheetsClient._ordered_insert_row(col, "Aula 10") == 4


def test_ultima_parte_vai_pro_final_e_nao_confunde_aula_1_com_aula_10():
    col = ["Aula", "Aula 1 - Parte 2", "Aula 10 - Parte 1"]
    assert SheetsClient._ordered_insert_row(col, "Aula 10 - Parte 2") == 4
    assert SheetsClient._ordered_insert_row(col, "Aula 1 - Parte 1") == 2


def test_create_reaproveita_notebook_existente(monkeypatch):
    client = NotebookLMClient.__new__(NotebookLMClient)
    chamadas = []

    def fake_run(args, timeout):
        chamadas.append(args[0])
        return {"success": True, "error": None, "data": {"notebooks": [
            {"id": "velho", "title": "UC16 - Aula 10 - Parte 1", "created_at": "2026-09-29T22:07:07+00:00"},
            {"id": "outro", "title": "UC16 - Aula 10 - Parte 2"},
        ]}}

    monkeypatch.setattr(client, "_run_cli", fake_run)
    r = client.create_notebook("UC16 - Aula 10 - Parte 1")
    assert r == {"success": True, "notebook_id": "velho", "error": None}
    assert "create" not in chamadas
