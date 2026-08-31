"""Lock de execução única (single-instance) via arquivo atômico.

Usado pelo auto_pipeline pra garantir que só UMA execução processe aulas por
vez - se uma Tarefa Agendada e uma execução manual (ou duas rodadas sobrepostas)
dispararem juntas, uma delas aborta em vez de reprocessar as mesmas aulas e
duplicar chamadas de API / notificações.

Como funciona:
- Cria o arquivo de lock de forma atômica (O_CREAT|O_EXCL). Se a criação
  "ganhar", esta execução assumiu o lock.
- Se o arquivo já existe, está BLOQUEADO - a menos que o timestamp seja mais
  antigo que STALE_AFTER segundos (processo anterior morreu e deixou o arquivo
  pra trás); aí assume o lock no lugar.
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


class RunLock:
    def __init__(self, path) -> None:
        self.path = Path(path)
        self._acquired = False

    def acquire(self) -> bool:
        """Tenta assumir o lock. True = esta execução é a única agora."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, f"{time.time()}".encode())
            os.close(fd)
            self._acquired = True
            return True
        except FileExistsError:
            # Já existe um lock. É stale (dono morreu) ou ainda ativo?
            try:
                age = time.time() - self.path.stat().st_mtime
            except OSError:
                age = 0.0
            if age > STALE_AFTER_SECONDS:
                try:
                    self.path.unlink()
                    return self.acquire()
                except OSError:
                    return False  # corrida pra remover o stale - perdeu
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
