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


def test_aula_atrasada_entra_antes_da_aula_seguinte():
    col = ["Aula", "Aula 8", "Aula 10 - parte 2 - TCLE (PC)", "Aula 11 - Coorte"]
    assert SheetsClient._ordered_insert_row(col, "Aula 9") == 3
    assert SheetsClient._ordered_insert_row(col, "Aula 10 - parte 1") == 3
    assert SheetsClient._ordered_insert_row(col, "Leitura Prévia 9") == 5


def test_ordem_e_busca_ficam_na_secao_do_semestre_atual():
    col = [
        "1º Semestre - Resp. I", "Aula 11 - Sono", "Aula 12 - Parte 1 - Insuficiência", "",
        "2° semestre - Resp. II", "Aula 8 Parte 2 - FMUSP", "Aula 13 - TEP",
    ]
    assert SheetsClient._section_start(col) == 6
    assert SheetsClient._ordered_insert_row(col, "Aula 11") == 7
    assert SheetsClient._ordered_insert_row(col, "Aula 14") == 8
    assert SheetsClient._section_start(["Aula", "Aula 1", "Aula 2"]) == 2


def test_planilha_renumerada_casa_pelo_titulo():
    col = [
        "Aula", "Aula 3 - Parte 1 - Probabilidade II (MQ)",
        "Aula 9 - parte 1 - Intervalo de confiança (MQ))", "Aula 9 - parte 2 - TCLE (PC)",
        "Aula 10 - Estudo de Coorte (EP) ", "Aula 11 - parte 2 - Intervalo de confiança (MQ)",
    ]
    find = SheetsClient._find_by_title
    assert find(col, "Aula 10 - parte 1 - Intervalo de confiança (MQ))", 2) == 3
    assert find(col, "Aula 12 - parte 2 - Intervalo de confiança (MQ)", 2) == 6
    assert find(col, "Aula 11 - Estudo de Coorte (EP)", 2) == 5
    # genérico demais ("parte N" sem título) ou inexistente: não casa
    assert find(col, "Aula 4 - Parte 1", 2) is None
    assert find(col, "Aula 13 - Revisão sistemática", 2) is None


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


def test_artefato_pending_conta_como_ja_disparado(monkeypatch):
    client = NotebookLMClient.__new__(NotebookLMClient)
    monkeypatch.setattr(client, "_run_cli", lambda args, timeout: {"success": True, "error": None, "data": {"artifacts": [
        {"type_id": "video", "status": "pending"},
        {"type_id": "audio", "status": "completed"},
        {"type_id": "slide_deck", "status": "failed"},
    ]}})
    assert client.list_existing_artifact_types("nb") == {"video", "audio"}
