import os
import sqlite3
from pathlib import Path
from typing import List, Dict, Any, Optional
from utils.logger import logger

class DatabaseManager:
    """Gerencia o banco de dados SQLite local para controle de aulas processadas.

    Cloud Run Jobs não tem disco persistente entre execuções - cada rodada começa
    com um filesystem novo. Se a env var GCS_DB_BUCKET estiver configurada, este
    banco é baixado de um bucket do Cloud Storage na inicialização e reenviado
    de volta depois de cada gravação, pra não perder o controle de "aula já
    processada" entre uma execução e a próxima. Sem essa env var (uso local, no
    Windows), o comportamento é 100% o mesmo de sempre: só o arquivo local."""

    def __init__(self, db_path: str = "data/med_automator.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._gcs_bucket_name = os.environ.get("GCS_DB_BUCKET")
        self._gcs_blob_name = os.environ.get("GCS_DB_BLOB", self.db_path.name)
        self._download_db_from_gcs()
        self._init_db()

    def _get_gcs_bucket(self):
        """Lazy: só importa google-cloud-storage se GCS_DB_BUCKET estiver configurado
        (dependência opcional, não precisa estar instalada pra uso local)."""
        if not self._gcs_bucket_name:
            return None
        try:
            from google.cloud import storage
        except ImportError:
            logger.warning("GCS_DB_BUCKET configurado mas google-cloud-storage não está instalado - ignorando.")
            return None
        try:
            client = storage.Client()
            return client.bucket(self._gcs_bucket_name)
        except Exception as e:
            logger.error(f"Falha ao conectar no bucket GCS '{self._gcs_bucket_name}': {e}")
            return None

    def _download_db_from_gcs(self) -> None:
        """Baixa o .db do bucket ANTES de abrir a conexão, se existir um lá (roda
        uma vez, no __init__). Se o blob ainda não existir (primeira execução),
        segue com um banco local novo - _init_db cria as tabelas normalmente."""
        bucket = self._get_gcs_bucket()
        if not bucket:
            return
        blob = bucket.blob(self._gcs_blob_name)
        try:
            if blob.exists():
                blob.download_to_filename(str(self.db_path))
                logger.info(f"Banco de dados baixado do GCS: gs://{self._gcs_bucket_name}/{self._gcs_blob_name}")
            else:
                logger.info(f"Nenhum banco de dados encontrado ainda em gs://{self._gcs_bucket_name}/{self._gcs_blob_name} - começando do zero.")
        except Exception as e:
            logger.error(f"Falha ao baixar o banco de dados do GCS (seguindo com o estado local): {e}")

    def _upload_db_to_gcs(self) -> None:
        """Sobe o .db pro bucket depois de cada gravação bem-sucedida - mantém a
        cópia remota sempre atualizada por aula, não só no fim da execução (se o
        job cair no meio, as aulas já gravadas até ali não se perdem)."""
        bucket = self._get_gcs_bucket()
        if not bucket:
            return
        try:
            blob = bucket.blob(self._gcs_blob_name)
            blob.upload_from_filename(str(self.db_path))
        except Exception as e:
            logger.error(f"Falha ao subir o banco de dados pro GCS: {e}")

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        """Cria a tabela de controle de aulas se ela não existir."""
        try:
            with self._get_connection() as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS completed_lessons (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        unit_code TEXT NOT NULL,
                        lesson_name TEXT NOT NULL,
                        notebook_id TEXT,
                        status TEXT DEFAULT 'success',
                        details TEXT,
                        processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        anki_synced_at TIMESTAMP,
                        UNIQUE(unit_code, lesson_name)
                    )
                """)
                # Migração leve para bancos criados antes das colunas abaixo existirem.
                for column_def in ("status TEXT DEFAULT 'success'", "details TEXT", "anki_synced_at TIMESTAMP"):
                    try:
                        conn.execute(f"ALTER TABLE completed_lessons ADD COLUMN {column_def}")
                    except sqlite3.OperationalError:
                        pass  # coluna já existe

                # Contador de chamadas reais à API do Gemini por dia (UTC) - usado por
                # core/multimodal_processor.py pra nunca ultrapassar a cota diária do
                # tier gratuito (20 requisições/dia). Uma linha por dia, incrementada a
                # cada tentativa de verdade (sucesso OU falha - a cota do Google conta
                # as duas do mesmo jeito).
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS gemini_daily_usage (
                        usage_date TEXT PRIMARY KEY,
                        request_count INTEGER NOT NULL DEFAULT 0
                    )
                """)
                conn.commit()
        except Exception as e:
            logger.error(f"Erro ao inicializar o banco de dados: {e}")

    def mark_lesson_completed(
        self,
        unit_code: str,
        lesson_name: str,
        notebook_id: str,
        status: str = "success",
        details: str = None,
    ):
        """Registra o resultado do processamento de uma aula e salva o ID do NotebookLM.

        `status` deve refletir honestamente o que aconteceu (ex.: 'success',
        'partial_failure', 'error') - nunca 'success' quando uma etapa crítica falhou.
        `details` pode trazer um resumo curto do que deu errado, quando aplicável.
        """
        try:
            with self._get_connection() as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO completed_lessons (unit_code, lesson_name, notebook_id, status, details)
                    VALUES (?, ?, ?, ?, ?)
                """, (unit_code, lesson_name, notebook_id, status, details))
                conn.commit()
            logger.info(f"Aula {lesson_name} salva no banco de dados local (status={status}).")
            self._upload_db_to_gcs()
        except Exception as e:
            logger.error(f"Erro ao salvar aula no banco de dados: {e}")

    def get_lesson_status(self, unit_code: str, lesson_name: str) -> Optional[Dict[str, Any]]:
        """Retorna o registro salvo para essa aula específica (status/details/notebook_id),
        ou None se ela nunca foi processada antes."""
        try:
            with self._get_connection() as conn:
                cursor = conn.execute("""
                    SELECT lesson_name, notebook_id, status, details, processed_at
                    FROM completed_lessons
                    WHERE unit_code = ? AND lesson_name = ?
                """, (unit_code, lesson_name))
                row = cursor.fetchone()
                if row:
                    return {
                        "lesson_name": row["lesson_name"],
                        "notebook_id": row["notebook_id"],
                        "status": row["status"] if row["status"] else "success",
                        "details": row["details"],
                        "processed_at": row["processed_at"],
                    }
        except Exception as e:
            logger.error(f"Erro ao buscar status da aula {lesson_name}: {e}")
        return None

    def is_lesson_completed(self, unit_code: str, lesson_name: str) -> bool:
        """Checagem rápida: essa aula específica já foi processada com SUCESSO?

        Só conta como concluída quando status='success' - uma aula que falhou
        (partial_failure/error) NÃO conta como concluída, pra ser automaticamente
        retentada na próxima execução em vez de ficar pulada pra sempre."""
        status_row = self.get_lesson_status(unit_code, lesson_name)
        return bool(status_row and status_row["status"] == "success")

    def get_completed_lessons(self, unit_code: str) -> List[Dict[str, Any]]:
        """Retorna a lista de aulas já processadas para uma unidade curricular."""
        lessons = []
        try:
            with self._get_connection() as conn:
                cursor = conn.execute("""
                    SELECT lesson_name, notebook_id, status, details, processed_at
                    FROM completed_lessons
                    WHERE unit_code = ?
                    ORDER BY processed_at DESC
                """, (unit_code,))
                rows = cursor.fetchall()
                for row in rows:
                    lessons.append({
                        "lesson_name": row["lesson_name"],
                        "notebook_id": row["notebook_id"],
                        "status": row["status"] if row["status"] else "success",
                        "details": row["details"],
                        "processed_at": row["processed_at"]
                    })
        except Exception as e:
            logger.error(f"Erro ao buscar aulas concluídas: {e}")
        return lessons

    def get_lessons_pending_anki_sync(self) -> List[Dict[str, Any]]:
        """Aulas concluídas com sucesso (em QUALQUER unidade) que ainda não foram
        importadas no Anki desta máquina via AnkiConnect - usado pelo script
        sync_cloud_flashcards_to_anki.py (aulas processadas pelo Cloud Run não
        chegam ao Anki na hora, porque o container não alcança o localhost da
        usuária; esse script tardio fecha essa lacuna quando roda localmente)."""
        lessons = []
        try:
            with self._get_connection() as conn:
                cursor = conn.execute("""
                    SELECT unit_code, lesson_name, notebook_id, processed_at
                    FROM completed_lessons
                    WHERE status = 'success' AND anki_synced_at IS NULL
                    ORDER BY processed_at ASC
                """)
                for row in cursor.fetchall():
                    lessons.append({
                        "unit_code": row["unit_code"],
                        "lesson_name": row["lesson_name"],
                        "notebook_id": row["notebook_id"],
                        "processed_at": row["processed_at"],
                    })
        except Exception as e:
            logger.error(f"Erro ao buscar aulas pendentes de sincronização com o Anki: {e}")
        return lessons

    def mark_anki_synced(self, unit_code: str, lesson_name: str) -> None:
        """Marca uma aula como já importada no Anki local (anki_synced_at =
        agora), pra sync_cloud_flashcards_to_anki.py não tentar de novo a cada
        execução."""
        try:
            with self._get_connection() as conn:
                conn.execute("""
                    UPDATE completed_lessons SET anki_synced_at = CURRENT_TIMESTAMP
                    WHERE unit_code = ? AND lesson_name = ?
                """, (unit_code, lesson_name))
                conn.commit()
            logger.info(f"Aula {lesson_name} marcada como sincronizada com o Anki.")
            self._upload_db_to_gcs()
        except Exception as e:
            logger.error(f"Erro ao marcar aula {lesson_name} como sincronizada com o Anki: {e}")

    @staticmethod
    def _today_utc() -> str:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def get_gemini_request_count_today(self) -> int:
        """Quantas chamadas reais à API do Gemini já foram feitas hoje (UTC) -
        usado pra decidir ANTES de tentar uma chamada nova se ainda cabe na cota
        diária do tier gratuito (20/dia), em vez de só descobrir depois que a
        API já rejeitou."""
        try:
            with self._get_connection() as conn:
                row = conn.execute(
                    "SELECT request_count FROM gemini_daily_usage WHERE usage_date = ?",
                    (self._today_utc(),),
                ).fetchone()
                return row["request_count"] if row else 0
        except Exception as e:
            logger.error(f"Erro ao ler contagem de requisições do Gemini hoje: {e}")
            return 0  # em dúvida, não bloqueia - deixa a própria API rejeitar se precisar

    def increment_gemini_request_count(self) -> int:
        """Registra UMA chamada real à API do Gemini feita agora (sucesso OU
        falha - a cota do Google conta as duas do mesmo jeito) e devolve o novo
        total de hoje. Chamar isso ANTES de cada tentativa de verdade, nunca
        depois - senão uma falha de rede antes de confirmar a contagem faria a
        gente perder a conta real."""
        today = self._today_utc()
        try:
            with self._get_connection() as conn:
                conn.execute("""
                    INSERT INTO gemini_daily_usage (usage_date, request_count) VALUES (?, 1)
                    ON CONFLICT(usage_date) DO UPDATE SET request_count = request_count + 1
                """, (today,))
                new_count = conn.execute(
                    "SELECT request_count FROM gemini_daily_usage WHERE usage_date = ?", (today,)
                ).fetchone()["request_count"]
                conn.commit()
            self._upload_db_to_gcs()
            return new_count
        except Exception as e:
            logger.error(f"Erro ao registrar requisição do Gemini de hoje: {e}")
            return self.get_gemini_request_count_today()


db_manager = DatabaseManager()