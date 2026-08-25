import subprocess
import json
import shutil
import sys
import time
from pathlib import Path
from typing import Optional, List, Dict, Any
from utils.logger import logger

# Caminho do executável da CLI notebooklm. Resolvido a partir de sys.executable
# (o notebooklm.exe fica ao lado do python.exe dentro de venv/Scripts) em vez de
# depender do bare "notebooklm" estar no PATH do processo - isso quebra quando o
# script roda "puro" (venv\Scripts\python.exe auto_pipeline.py, ou via Tarefa
# Agendada do Windows), já que só ativar o venv manualmente ajusta o PATH.
_notebooklm_sibling = Path(sys.executable).parent / "notebooklm.exe"
NOTEBOOKLM_CLI = str(_notebooklm_sibling) if _notebooklm_sibling.exists() else (shutil.which("notebooklm") or "notebooklm")

# Timeouts (segundos) para cada operação da CLI notebooklm-py. Os valores antigos
# (10s/25s fixos) eram curtos demais para uploads e geração de estúdio reais.
CREATE_TIMEOUT_SECONDS = 30
SOURCE_ADD_TIMEOUT_SECONDS = 120
SOURCE_WAIT_TIMEOUT_SECONDS = 180
AUTH_CHECK_TIMEOUT_SECONDS = 30

# Pequena espera de cortesia antes de checar a indexação da fonte, para dar tempo
# do NotebookLM registrar a fonte no backend antes do primeiro "source wait".
SOURCE_INDEX_GRACE_SECONDS = 3

# "source add" já se mostrou flaky na prática (a CLI pode entrar num loop de erro
# assíncrono interno e falhar de forma transitória) - vale uma retentativa curta
# antes de desistir, importante pra rodar sozinho de madrugada sem intervenção.
SOURCE_ADD_MAX_ATTEMPTS = 2
SOURCE_ADD_RETRY_WAIT_SECONDS = 5

# Espera final por TODAS as fontes do notebook (não só as que este processo acabou
# de adicionar) antes de disparar a geração do Estúdio - pega fontes reaproveitadas
# de tentativas anteriores e fontes "fantasma" deixadas por um 'source add' que
# falhou (a CLI documenta que essas ficam presas em 'preparing' pra sempre, como
# evidência do erro). Generoso de propósito: melhor esperar mais do que gerar com
# fonte incompleta.
SOURCES_READY_TIMEOUT_SECONDS = 300
SOURCES_READY_POLL_INTERVAL_SECONDS = 10
PENDING_SOURCE_STATUSES = ("preparing", "processing", "unknown")

# Teto de tamanho pro texto de erro logado/guardado (stderr da CLI pode, em bugs
# raros dela mesma, cuspir milhares de linhas repetidas de traceback interno -
# sem isso, uma única falha pode inflar o log e a coluna `details` do banco).
MAX_ERROR_TEXT_CHARS = 4000


def _truncate_for_log(text: str, limit: int = MAX_ERROR_TEXT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"... [truncado - {len(text)} chars no total]"

# Disparo fire-and-forget de cada "generate <tipo>": só espera a CLI confirmar que
# aceitou o pedido (retorna um task_id), NÃO espera a geração terminar. O timeout
# aqui cobre só a chamada de disparo em si, por isso é curto.
GENERATE_FIRE_TIMEOUT_SECONDS = 60

# Todos os artefatos do Estúdio gerados automaticamente para cada notebook novo,
# uma única vez, depois que todas as fontes já foram adicionadas e indexadas.
# Cada um é apenas disparado (--no-wait, default da CLI) e a conferência de que a
# geração de fato terminou fica por conta do usuário no próprio NotebookLM depois.
STUDIO_ARTIFACT_SPECS = [
    {"key": "audio", "args": ["generate", "audio"]},
    {"key": "report", "args": ["generate", "report", "--format", "study-guide"]},
    {"key": "flashcards", "args": ["generate", "flashcards", "--difficulty", "hard", "--quantity", "more"]},
    {"key": "quiz", "args": ["generate", "quiz", "--difficulty", "hard", "--quantity", "more"]},
    {"key": "slide_deck", "args": ["generate", "slide-deck"]},
    {"key": "video", "args": ["generate", "video"]},
    {"key": "infographic", "args": ["generate", "infographic"]},
    # "data-table" exige uma descrição (não tem comportamento default sem argumento,
    # ao contrário dos outros tipos) - por isso passamos uma descrição genérica.
    {"key": "data_table", "args": ["generate", "data-table", "Tabela com os principais conceitos, comparações e dados da aula"]},
]

# "generate mind-map" é a única exceção: a CLI não tem --wait/--no-wait para ele -
# ela sempre faz o polling internamente e só retorna quando termina (ou falha).
# Não tem como disparar sem esperar aqui; mantemos um timeout generoso nosso só
# como teto de segurança para não travar para sempre.
MIND_MAP_TIMEOUT_SECONDS = 600


class NotebookLMClient:
    """Interface para interagir com o NotebookLM via CLI do notebooklm-py.

    Fluxo esperado: criar notebook -> adicionar TODAS as fontes (aguardando cada
    uma indexar) -> disparar TODOS os artefatos do Estúdio (áudio, relatório,
    flashcards, teste, slides, vídeo, infográfico, tabela de dados e mapa mental)
    UMA ÚNICA VEZ, depois que todas as fontes estiverem prontas.
    """

    def __init__(self):
        pass

    def _run_cli(self, args: List[str], timeout: int) -> Dict[str, Any]:
        """Executa um comando da CLI notebooklm e devolve um dict rico com
        success/error/data, sempre logando stderr (e stdout relevante) em caso de falha
        para dar visibilidade real do que deu errado."""
        cmd = [NOTEBOOKLM_CLI] + args + ["--json"]
        printable_cmd = " ".join(cmd)
        logger.info(f"Executando CLI: {printable_cmd}")

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as e:
            stderr_txt = _truncate_for_log((e.stderr or "").strip() if isinstance(e.stderr, str) else "")
            logger.error(
                f"Comando CLI expirou após {timeout}s: {printable_cmd}"
                + (f" | stderr parcial: {stderr_txt}" if stderr_txt else "")
            )
            return {"success": False, "error": f"timeout após {timeout}s", "data": None}
        except Exception as e:
            logger.error(f"Falha ao executar CLI NotebookLM ({printable_cmd}): {e}")
            return {"success": False, "error": str(e), "data": None}

        # Versão completa (pra parsear o JSON de verdade) e versão truncada (só pra
        # log/mensagem de erro - stderr da CLI já mostrou, em bug real dela, cuspir
        # milhares de linhas repetidas de traceback interno numa única falha).
        stderr_full = (result.stderr or "").strip()
        stdout_full = (result.stdout or "").strip()
        stderr_log = _truncate_for_log(stderr_full)
        stdout_log = _truncate_for_log(stdout_full)

        if stderr_log:
            # Sempre loga o stderr, mesmo em caso de sucesso (a CLI usa stderr para warnings).
            log_fn = logger.warning if result.returncode == 0 else logger.error
            log_fn(f"stderr do comando '{' '.join(args)}': {stderr_log}")

        if result.returncode != 0:
            logger.error(
                f"Comando CLI '{' '.join(args)}' falhou (exit code {result.returncode})."
                + (f" stdout: {stdout_log}" if stdout_log and not stderr_log else "")
            )
            error_msg = stderr_log or stdout_log or f"exit code {result.returncode}"
            return {"success": False, "error": error_msg, "data": None}

        data = None
        if stdout_full:
            try:
                data = json.loads(stdout_full)
            except json.JSONDecodeError:
                logger.debug(f"Saída de '{' '.join(args)}' não é JSON válido: {stdout_log[:300]}")

        return {"success": True, "error": None, "data": data}

    def check_auth(self) -> Dict[str, Any]:
        """Verifica se a sessão do NotebookLM CLI está válida DE VERDADE (checagem
        de rede, somente leitura - não rotaciona cookies nem escreve em disco).

        Feito pra ser chamado logo no início de uma execução automática, pra falhar
        rápido e visível ANTES de começar a processar qualquer aula, em vez de só
        descobrir a autenticação expirada no meio do trabalho."""
        return self._run_cli(["auth", "check", "--test", "--passive"], timeout=AUTH_CHECK_TIMEOUT_SECONDS)

    def notebook_exists(self, notebook_id: str) -> bool:
        """Confere se um notebook_id ainda existe de verdade no NotebookLM (ex.:
        pra decidir se vale reaproveitar o notebook de uma tentativa anterior, em
        vez de assumir cegamente que ele continua lá)."""
        result = self._run_cli(["use", notebook_id], timeout=CREATE_TIMEOUT_SECONDS)
        return result["success"]

    def list_ready_source_titles(self, notebook_id: str) -> set:
        """Retorna os títulos das fontes já indexadas com sucesso (status 'ready')
        num notebook - usado pra não re-adicionar (duplicar) fontes ao reaproveitar
        um notebook de uma tentativa anterior. Fontes 'preparing'/'error' (sobras de
        um 'source add' que falhou antes) NÃO contam como já presentes - a fonte
        real ainda precisa ser adicionada de novo."""
        result = self._run_cli(["source", "list", "-n", notebook_id], timeout=SOURCE_ADD_TIMEOUT_SECONDS)
        if not result["success"]:
            logger.warning(f"Não consegui listar as fontes existentes do notebook {notebook_id}: {result['error']}")
            return set()
        sources = (result["data"] or {}).get("sources", [])
        return {s["title"] for s in sources if s.get("status") == "ready" and s.get("title")}

    def wait_for_sources_ready(
        self, notebook_id: str, timeout: int = SOURCES_READY_TIMEOUT_SECONDS
    ) -> Dict[str, Any]:
        """Espera TODAS as fontes do notebook (independente de terem sido adicionadas
        nesta execução ou reaproveitadas de uma anterior) saírem de
        'preparing'/'processing'/'unknown' antes de liberar a geração do Estúdio.

        Diferente do 'source wait' individual (que só cobre a fonte que este processo
        acabou de adicionar com sucesso), essa checagem relê a lista completa de fontes
        do notebook - pegando também fontes "fantasma" deixadas por um 'source add'
        que falhou antes de retornar um ID (ficam presas em 'preparing' pra sempre).

        Retorna {"success": True} quando tudo chegou num estado terminal (ready/error),
        ou {"success": False, "pending": [...]} se ainda sobrou algo pendente depois do
        timeout - quem chama decide o que fazer (ex.: limpar as fontes presas)."""
        deadline = time.time() + timeout
        while True:
            result = self._run_cli(["source", "list", "-n", notebook_id], timeout=SOURCE_ADD_TIMEOUT_SECONDS)
            if not result["success"]:
                logger.warning(f"Não consegui checar o status das fontes do notebook {notebook_id}: {result['error']}")
                return {"success": False, "pending": [], "error": result["error"]}

            sources = (result["data"] or {}).get("sources", [])
            pending = [s for s in sources if s.get("status") in PENDING_SOURCE_STATUSES]

            if not pending:
                logger.info(f"Todas as fontes do notebook {notebook_id} estão prontas (ready/error).")
                return {"success": True, "pending": [], "error": None}

            if time.time() >= deadline:
                pending_desc = [f"{s.get('title')} ({s.get('status')})" for s in pending]
                logger.warning(
                    f"{len(pending)} fonte(s) do notebook {notebook_id} ainda não terminaram de "
                    f"processar após {timeout}s - provavelmente sobras de um 'source add' que falhou "
                    f"antes (ficam presas em 'preparing' pra sempre, por design da CLI): {pending_desc}"
                )
                return {"success": False, "pending": pending, "error": f"{len(pending)} fonte(s) presas em processamento"}

            logger.info(
                f"Aguardando {len(pending)} fonte(s) do notebook {notebook_id} terminarem de processar "
                f"antes de gerar o Estúdio (checando de novo em {SOURCES_READY_POLL_INTERVAL_SECONDS}s)..."
            )
            time.sleep(SOURCES_READY_POLL_INTERVAL_SECONDS)

    def cleanup_stuck_sources(self, notebook_id: str, pending_sources: List[Dict[str, Any]]) -> int:
        """Apaga fontes ainda presas (preparing/processing/unknown) depois que
        wait_for_sources_ready esgotou o timeout - são sobras "fantasma" de um
        'source add' que falhou e nunca vão terminar de processar sozinhas.
        Retorna quantas foram apagadas de fato."""
        deleted = 0
        for s in pending_sources:
            source_id = s.get("id")
            title = s.get("title")
            if not source_id:
                continue
            result = self._run_cli(["source", "delete", source_id, "-n", notebook_id, "-y"], timeout=CREATE_TIMEOUT_SECONDS)
            if result["success"]:
                logger.info(f"Fonte fantasma removida: '{title}' (id={source_id}, estava '{s.get('status')}').")
                deleted += 1
            else:
                logger.warning(f"Não consegui remover a fonte fantasma '{title}' (id={source_id}): {result['error']}")
        return deleted

    def delete_all_artifacts(self, notebook_id: str) -> int:
        """Apaga TODOS os artefatos do Estúdio de um notebook (ex.: pra regenerar do
        zero depois de descobrir que foram gerados com fontes incompletas). Retorna
        quantos foram apagados de fato."""
        list_result = self._run_cli(["artifact", "list", "-n", notebook_id], timeout=SOURCE_ADD_TIMEOUT_SECONDS)
        if not list_result["success"]:
            logger.warning(f"Não consegui listar os artefatos do notebook {notebook_id} pra apagar: {list_result['error']}")
            return 0
        artifacts = (list_result["data"] or {}).get("artifacts", [])
        deleted = 0
        for a in artifacts:
            artifact_id = a.get("id")
            title = a.get("title")
            if not artifact_id:
                continue
            result = self._run_cli(["artifact", "delete", artifact_id, "-n", notebook_id, "-y"], timeout=CREATE_TIMEOUT_SECONDS)
            if result["success"]:
                logger.info(f"Artefato removido: '{title}' ({a.get('type_id')}, id={artifact_id}).")
                deleted += 1
            else:
                logger.warning(f"Não consegui remover o artefato '{title}' (id={artifact_id}): {result['error']}")
        return deleted

    def list_existing_artifact_types(self, notebook_id: str) -> set:
        """Retorna os type_id dos artefatos do Estúdio já disparados (completos ou
        ainda em andamento) num notebook - usado pra não disparar de novo (duplicar)
        artefatos ao reaproveitar um notebook de uma tentativa anterior."""
        result = self._run_cli(["artifact", "list", "-n", notebook_id], timeout=SOURCE_ADD_TIMEOUT_SECONDS)
        if not result["success"]:
            logger.warning(f"Não consegui listar os artefatos existentes do notebook {notebook_id}: {result['error']}")
            return set()
        artifacts = (result["data"] or {}).get("artifacts", [])
        return {a["type_id"] for a in artifacts if a.get("status") in ("completed", "in_progress") and a.get("type_id")}

    def create_notebook(self, title: str) -> Dict[str, Any]:
        """Cria um notebook e retorna um dict {"success", "notebook_id", "error"}.

        Retorna o erro real (ex.: "Authentication expired or invalid...") em vez de
        só None, pra quem chama conseguir propagar isso pro log/status final -
        importante pra rodar sozinho de madrugada sem ninguém olhando na hora."""
        result = self._run_cli(["create", title], timeout=CREATE_TIMEOUT_SECONDS)
        if not result["success"]:
            logger.error(f"Falha ao criar notebook '{title}': {result['error']}")
            return {"success": False, "notebook_id": None, "error": result["error"]}

        data = result["data"] or {}
        notebook_id = (
            data["notebook"].get("id")
            if "notebook" in data and isinstance(data["notebook"], dict)
            else data.get("id")
        )
        if not notebook_id:
            logger.error(f"Notebook '{title}' criado mas a resposta da CLI não trouxe um ID: {data}")
            return {"success": False, "notebook_id": None, "error": "resposta da CLI sem notebook_id"}

        return {"success": True, "notebook_id": notebook_id, "error": None}

    def add_source_to_notebook(self, notebook_id: str, file_path: Path) -> Dict[str, Any]:
        """Adiciona um único arquivo local como fonte e aguarda o NotebookLM terminar
        de indexá-la. NÃO dispara a geração do Estúdio (isso é feito uma única vez,
        depois de todas as fontes prontas, por generate_studio_artifacts).

        Retorna um dict {"success", "source_id", "error"} para o chamador decidir
        corretamente o que fazer em caso de falha (em vez de um bool "cego").
        """
        if not file_path or not file_path.exists():
            error_msg = f"arquivo não encontrado: {file_path}"
            logger.error(f"Não foi possível adicionar fonte: {error_msg}")
            return {"success": False, "source_id": None, "error": error_msg}

        # "source add" já se mostrou flaky na prática (a CLI pode falhar de forma
        # transitória) - tenta de novo antes de desistir, importante rodando sozinho.
        add_result = None
        for attempt in range(1, SOURCE_ADD_MAX_ATTEMPTS + 1):
            add_result = self._run_cli(
                ["source", "add", str(file_path.absolute()), "-n", notebook_id],
                timeout=SOURCE_ADD_TIMEOUT_SECONDS,
            )
            if add_result["success"]:
                break
            if attempt < SOURCE_ADD_MAX_ATTEMPTS:
                logger.warning(
                    f"Falha ao adicionar fonte {file_path.name} (tentativa {attempt}/"
                    f"{SOURCE_ADD_MAX_ATTEMPTS}): {add_result['error']} - tentando de novo "
                    f"em {SOURCE_ADD_RETRY_WAIT_SECONDS}s..."
                )
                time.sleep(SOURCE_ADD_RETRY_WAIT_SECONDS)

        if not add_result["success"]:
            logger.error(
                f"Falha ao adicionar fonte {file_path.name} após {SOURCE_ADD_MAX_ATTEMPTS} "
                f"tentativa(s): {add_result['error']}"
            )
            return {"success": False, "source_id": None, "error": add_result["error"]}

        data = add_result["data"] or {}
        source = data.get("source")
        source_id = source.get("id") if isinstance(source, dict) else data.get("id")

        if not source_id:
            logger.warning(
                f"Fonte {file_path.name} adicionada, mas o ID não veio na resposta da CLI; "
                f"pulando a espera de indexação."
            )
            return {"success": True, "source_id": None, "error": None}

        # Dá um instante para o backend registrar a fonte antes de checar o status.
        time.sleep(SOURCE_INDEX_GRACE_SECONDS)

        logger.info(f"Aguardando indexação da fonte {file_path.name} (ID: {source_id})...")
        wait_result = self._run_cli(
            ["source", "wait", source_id, "-n", notebook_id, "--timeout", str(SOURCE_WAIT_TIMEOUT_SECONDS)],
            timeout=SOURCE_WAIT_TIMEOUT_SECONDS + 30,
        )
        if not wait_result["success"]:
            logger.warning(
                f"Fonte {file_path.name} foi adicionada mas não terminou de indexar: {wait_result['error']}"
            )
            return {"success": False, "source_id": source_id, "error": f"indexação falhou: {wait_result['error']}"}

        logger.info(f"Fonte {file_path.name} indexada com sucesso.")
        return {"success": True, "source_id": source_id, "error": None}

    def add_sources_to_notebook(
        self, notebook_id: str, file_paths: List[Path], skip_titles: Optional[set] = None
    ) -> Dict[str, Any]:
        """Adiciona múltiplas fontes em sequência, aguardando cada uma indexar.
        Continua tentando as fontes seguintes mesmo se uma falhar, mas reporta
        honestamente quais deram certo e quais não.

        `skip_titles` (opcional): nomes de arquivo que já estão indexados com
        sucesso no notebook (ex.: ao reaproveitar um notebook de uma tentativa
        anterior) - essas são puladas em vez de re-adicionadas (duplicadas)."""
        skip_titles = skip_titles or set()
        per_source_results = []
        for fp in file_paths:
            name = getattr(fp, "name", str(fp))
            if name in skip_titles:
                logger.info(f"Fonte '{name}' já está indexada no notebook reaproveitado - pulando novo upload.")
                per_source_results.append({"file": name, "success": True, "source_id": None, "error": None})
                continue
            r = self.add_source_to_notebook(notebook_id, fp)
            per_source_results.append({"file": name, **r})

        all_success = all(r["success"] for r in per_source_results)
        return {"success": all_success, "sources": per_source_results}

    def generate_studio_artifacts(self, notebook_id: str, skip_types: Optional[set] = None) -> Dict[str, Any]:
        """Dispara TODOS os artefatos do Estúdio (áudio, relatório, flashcards difícil/mais,
        teste difícil/mais, slides, vídeo, infográfico, tabela de dados e mapa mental)
        UMA ÚNICA VEZ por notebook, depois que todas as fontes já foram adicionadas e
        indexadas.

        Puro fire-and-forget: só dispara cada geração e confere se o COMANDO em si foi
        aceito pela CLI (erro real de CLI continua logado com o motivo, como em qualquer
        outro passo) - não espera nenhuma geração terminar. A conferência de que os
        artefatos realmente ficaram prontos é manual, direto no NotebookLM.

        Exceção: "generate mind-map" não tem um modo fire-and-forget na CLI (ela sempre
        bloqueia até terminar ou falhar) - por isso é a única chamada aqui que pode
        demorar de verdade.

        `skip_types` (opcional): type_id de artefatos já disparados (completos ou em
        andamento) num notebook reaproveitado de uma tentativa anterior - esses são
        pulados em vez de disparados de novo (duplicados).
        """
        skip_types = skip_types or set()
        results: Dict[str, Any] = {}

        for spec in STUDIO_ARTIFACT_SPECS:
            if spec["key"] in skip_types:
                logger.info(f"Artefato '{spec['key']}' já foi disparado no notebook reaproveitado - pulando.")
                results[spec["key"]] = {"success": True, "error": None}
                continue
            logger.info(f"Disparando geração de '{spec['key']}' no Estúdio do NotebookLM...")
            fire_result = self._run_cli(
                spec["args"] + ["-n", notebook_id],
                timeout=GENERATE_FIRE_TIMEOUT_SECONDS,
            )
            if fire_result["success"]:
                task_id = (fire_result["data"] or {}).get("task_id")
                logger.info(f"Geração de '{spec['key']}' disparada com sucesso (task_id={task_id}).")
            else:
                logger.warning(f"Falha ao disparar geração de '{spec['key']}': {fire_result['error']}")
            results[spec["key"]] = {"success": fire_result["success"], "error": fire_result["error"]}

        if "mind_map" in skip_types:
            logger.info("Artefato 'mind_map' já foi disparado no notebook reaproveitado - pulando.")
            results["mind_map"] = {"success": True, "error": None}
        else:
            # "generate mind-map" não tem modo fire-and-forget: já bloqueia até terminar.
            logger.info("Solicitando geração de 'mind_map' no Estúdio do NotebookLM (chamada bloqueante)...")
            mind_map_result = self._run_cli(
                ["generate", "mind-map", "-n", notebook_id],
                timeout=MIND_MAP_TIMEOUT_SECONDS,
            )
            if mind_map_result["success"]:
                logger.info("Mapa mental gerado com sucesso.")
            else:
                logger.warning(f"Falha ao gerar mapa mental: {mind_map_result['error']}")
            results["mind_map"] = {"success": mind_map_result["success"], "error": mind_map_result["error"]}

        overall_success = all(r.get("success") for r in results.values())
        if not overall_success:
            failed = [k for k, r in results.items() if not r.get("success")]
            logger.warning(f"Falha ao disparar um ou mais artefatos do Estúdio: {failed}")

        return {"success": overall_success, "artifacts": results}


notebooklm_client = NotebookLMClient()
