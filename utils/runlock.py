"""Lock de execução única (single-instance) via arquivo atômico.

Usado pelo auto_pipeline pra garantir que só UMA execução processe aulas por
vez - se uma Tarefa Agendada e uma execução manual (ou duas rodadas sobrepostas)
dispararem juntas, uma delas aborta em vez de reprocessar as mesmas aulas e
duplicar chamadas de API / notificações.

Como funciona:
- Cria o arquivo de lock de forma atômica (O_CREAT|O_EXCL). Se a criação
  "ganhar", esta execução assumiu o lock.
- Se o arquivo já existe, está BLOQUEADO - a menos que o PID gravado nele não
  exista mais (Unix) ou o timestamp seja mais antigo que STALE_AFTER segundos
  (processo anterior morreu e deixou o arquivo pra trás); aí assume o lock.
- Libera (apaga o arquivo) ao sair, via context manager / release().

Cross-platform: não usa fcntl/msvcrt (só Unix/Windows), então funciona igual no
Windows (dev) e no Raspberry Pi (produção).
"""
import os
import time
from pathlib import Path
from typing import Optional

# Segundos para considerar um lock "morto". O pipeline normalmente roda em
# minutos; um lock preso por muito mais que isso é quase certamente de um
# processo que morreu sem liberar (crash, kill, queda de energia no Pi).
STALE_AFTER_SECONDS = 7200  # 2h

# os.kill(pid, 0) só checa existência no Unix - no Windows ele MATA o processo.
_CAN_PROBE_PID = os.name != "nt"


class RunLock:
    def __init__(self, path) -> None:
        self.path = Path(path)
        self._acquired = False

    def acquire(self) -> bool:
        """Tenta assumir o lock. True = esta execução é a única agora."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, f"{time.time()} {os.getpid()}".encode())
            os.close(fd)
            self._acquired = True
            return True
        except FileExistsError:
            # Já existe um lock. É stale (dono morreu) ou ainda ativo?
            try:
                age = time.time() - self.path.stat().st_mtime
            except OSError:
                age = 0.0
            # Bug real corrigido: um `systemctl stop` no meio da execução deixava o
            # lock pra trás e as rodadas seguintes abortavam por até 2h. Com o PID
            # gravado, dono morto = stale na hora (o limite de tempo continua como
            # fallback pra lock no formato antigo, sem PID).
            if age > STALE_AFTER_SECONDS or self._owner_is_dead():
                try:
                    self.path.unlink()
                    return self.acquire()
                except OSError:
                    return False  # corrida pra remover o stale - perdeu
            return False

    def _owner_is_dead(self) -> bool:
        """True só quando o lock tem PID e esse processo não existe mais. Sem PID
        (formato antigo) ou sem como checar, assume vivo - na dúvida, não rouba."""
        try:
            parts = self.path.read_text().split()
            pid = int(parts[1]) if len(parts) > 1 else None
        except (OSError, ValueError):
            return False
        if pid is None or pid == os.getpid():
            return False
        if not _CAN_PROBE_PID:
            return False  # Windows: fica só o limite de tempo
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except OSError:
            return False  # existe, mas é de outro usuário
        return False

    def release(self) -> None:
        if self._acquired:
            try:
                self.path.unlink(missing_ok=True)
            finally:
                self._acquired = False

    # Permite `with RunLock(path) as ok:`.
    def __enter__(self) -> bool:
        return self.acquire()

    def __exit__(self, *exc) -> None:
        self.release()
