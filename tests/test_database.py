from database.db import DatabaseManager
from utils.hasher import compute_content_hash, compute_file_hash


def test_hash_generation(tmp_path):
    """Testa geração consistente de hash MD5."""
    test_file = tmp_path / "sample.txt"
    test_file.write_text("Conteudo de teste medico para calculo de hash MD5.", encoding="utf-8")

    hash1 = compute_file_hash(test_file)
    hash2 = compute_file_hash(test_file)
    assert hash1 == hash2
    assert len(hash1) == 32


def test_save_and_retrieve_lesson(tmp_path):
    """Testa persistência e busca de aula na tabela completed_lessons."""
    db_file = tmp_path / "test.db"
    db_mgr = DatabaseManager(db_path=str(db_file))

    db_mgr.mark_lesson_completed(
        unit_code="UC01",
        lesson_name="Aula_Teste_Imunologia",
        notebook_id="test_notebook_123",
        status="success",
        details="Processada com sucesso",
    )

    assert db_mgr.is_lesson_completed("UC01", "Aula_Teste_Imunologia") is True

    status = db_mgr.get_lesson_status("UC01", "Aula_Teste_Imunologia")
    assert status is not None
    assert status["lesson_name"] == "Aula_Teste_Imunologia"
    assert status["notebook_id"] == "test_notebook_123"
    assert status["status"] == "success"
