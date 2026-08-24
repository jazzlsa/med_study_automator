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
    """Testa persistência e busca de aula por hash e identificadores."""
    db_file = tmp_path / "test.db"
    db_mgr = DatabaseManager(db_path=db_file)

    sample_slide = tmp_path / "slide.pdf"
    sample_slide.write_text("Slide content mock", encoding="utf-8")

    c_hash = compute_content_hash(sample_slide)
    lesson_id = db_mgr.save_lesson(
        unit_code="UC01",
        lesson_name="Aula_Teste_Imunologia",
        content_hash=c_hash,
        cards_count=20,
    )
    assert lesson_id is not None

    lesson = db_mgr.get_lesson_by_hash(c_hash)
    assert lesson is not None
    assert lesson["unit_code"] == "UC01"
    assert lesson["cards_count"] == 20

    # Teste de Rollback / Deleção
    deleted = db_mgr.delete_lesson("UC01", "Aula_Teste_Imunologia")
    assert deleted is True
    assert db_mgr.get_lesson_by_hash(c_hash) is None