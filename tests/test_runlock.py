"""Testes do lock de execução única (utils/runlock.py)."""
import os
import time

from utils import runlock
from utils.runlock import RunLock, STALE_AFTER_SECONDS


def test_segunda_execucao_e_bloqueada(tmp_path):
    p = tmp_path / "x.lock"
    assert RunLock(p).acquire() is True
    assert RunLock(p).acquire() is False


def test_release_libera_o_lock(tmp_path):
    p = tmp_path / "x.lock"
    lock = RunLock(p)
    assert lock.acquire() is True
    lock.release()
    # depois de liberar, outra execução consegue assumir
    assert RunLock(p).acquire() is True


def test_lock_stale_e_assumido(tmp_path):
    p = tmp_path / "x.lock"
    p.write_text("restos de um processo que morreu")
    old = time.time() - (STALE_AFTER_SECONDS + 100)  # mais velho que o limite
    os.utime(p, (old, old))
    assert RunLock(p).acquire() is True


def test_lock_fresco_nao_e_assumido(tmp_path):
    p = tmp_path / "x.lock"
    p.write_text("ativo")
    # timestamp recente = dono ainda vivo
    assert RunLock(p).acquire() is False


def test_lock_de_processo_morto_e_assumido_na_hora(tmp_path, monkeypatch):
    p = tmp_path / "x.lock"
    p.write_text(f"{time.time()} 999999")  # fresco, mas o PID não existe
    monkeypatch.setattr(runlock, "_CAN_PROBE_PID", True)

    def fake_kill(pid, sig):
        raise ProcessLookupError

    monkeypatch.setattr(os, "kill", fake_kill)
    assert RunLock(p).acquire() is True


def test_lock_de_processo_vivo_nao_e_assumido(tmp_path, monkeypatch):
    p = tmp_path / "x.lock"
    p.write_text(f"{time.time()} 999999")
    monkeypatch.setattr(runlock, "_CAN_PROBE_PID", True)
    monkeypatch.setattr(os, "kill", lambda pid, sig: None)  # processo existe
    assert RunLock(p).acquire() is False


def test_context_manager(tmp_path):
    p = tmp_path / "x.lock"
    with RunLock(p) as ok:
        assert ok is True
        assert p.exists()
    assert not p.exists()  # liberado ao sair do with
