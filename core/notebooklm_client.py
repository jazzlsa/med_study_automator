import subprocess
import json
import shutil
import sys
import time
from pathlib import Path
from typing import Optional, List, Dict, Any
from core.file_sniff import guess_mime_type
from utils.logger import logger

# Caminho do executável da CLI notebooklm. Resolvido a partir de sys.executable
# (o notebooklm/notebooklm.exe fica ao lado do python dentro de venv/bin ou venv/Scripts)
# em vez de depender do bare "notebooklm" estar no PATH do processo.
_sibling_name = "notebooklm.exe" if sys.platform == "win32" else "notebooklm"
_notebooklm_sibling = Path(sys.executable).parent / _sibling_name
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

# "auth check" também se mostrou flaky em produção (2026-08-26: metade das
# execuções horárias do Cloud Run falharam com "token_fetch: false" mesmo com
# cookies/master_token válidos presentes - sintoma de instabilidade de rede
# transitória, não sessão realmente expirada) - antes disso, uma falha ISOLADA
# de rede nessa checagem abortava a execução inteira ANTES de tocar em qualquer
# aula, desperdiçando a rodada inteira daquela hora à toa.
AUTH_CHECK_MAX_ATTEMPTS = 3
AUTH_CHECK_RETRY_WAIT_SECONDS = 10

# Espera final por TODAS as fontes do notebook (não só as que este processo acabou
# de adicionar) antes de disparar a geração do Estúdio - pega fontes reaproveitadas
# de tentativas anteriores e fontes "fantasma" deixadas por um 'source add' que
# falhou (a CLI documenta que essas ficam presas em 'preparing' pra sempre, como
# evidência do erro). Generoso de propósito: áudio de aula real pode legitimamente
# demorar bem mais que alguns minutos pra indexar (bug real observado em produção
# com o timeout antigo de 300s: a espera estourava com o áudio ainda "preparing" e
# o Estúdio era gerado mesmo assim, sem essa fonte) - melhor esperar mais do que
# gerar com fonte incompleta. Se estourar mesmo assim, orchestrator.py NÃO apaga a
# fonte nem gera o Estúdio nesta rodada - deixa pro retry automático reconferir.
SOURCES_READY_TIMEOUT_SECONDS = 900
SOURCES_READY_POLL_INTERVAL_SECONDS = 15
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
# Idioma forçado explicitamente via "--language" em cada comando de geração que
# aceita a flag, em vez de confiar só na config de conta ("notebooklm language
# set") - bug real visto em produção: mesmo com a conta configurada pra pt_BR
# (e confirmada como "synced_to_server: true"), o slide_deck saiu em inglês
# ("Cardiac Engineering Atlas") enquanto os outros tipos saíram certos na MESMA
# rodada. "--language" tem prioridade maior que a config de conta (ordem real:
# --language > env NOTEBOOKLM_HL > config > 'en'), então é mais confiável.
# "flashcards" e "quiz" não têm essa flag (só a CLI de geração de imagem/texto
# longo tem) - continuam dependendo só da config de conta, que funcionou nos
# testes feitos até agora.
STUDIO_LANGUAGE = "pt_BR"

STUDIO_ARTIFACT_SPECS = [
    {"key": "audio", "args": ["generate", "audio", "--language", STUDIO_LANGUAGE]},
    {"key": "report", "args": ["generate", "report", "--format", "study-guide", "--language", STUDIO_LANGUAGE]},
    {"key": "flashcards", "args": ["generate", "flashcards", "--difficulty", "hard", "--quantity", "more"]},
    {"key": "quiz", "args": ["generate", "quiz", "--difficulty", "hard", "--quantity", "more"]},
    {"key": "slide_deck", "args": ["generate", "slide-deck", "--language", STUDIO_LANGUAGE]},
    {"key": "video", "args": ["generate", "video", "--language", STUDIO_LANGUAGE]},
    {"key": "infographic", "args": ["generate", "infographic", "--language", STUDIO_LANGUAGE]},
    # "data-table" exige uma descrição (não tem comportamento default sem argumento,
    # ao contrário dos outros tipos) - por isso passamos uma descrição genérica.
    {"key": "data_table", "args": ["generate", "data-table", "Tabela com os principais conceitos, comparações e dados da aula", "--language", STUDIO_LANGUAGE]},
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
        descobrir a autenticação expirada no meio do trabalho.

        Faz até AUTH_CHECK_MAX_ATTEMPTS tentativas com espera entre elas - essa
        checagem se mostrou flaky em produção por instabilidade de rede transitória
        (não por sessão de fato expirada), e sem retry uma única falha de rede
        isolada desperdiçava a execução horária inteira antes mesmo de começar."""
        last_result = None
        for attempt in range(1, AUTH_CHECK_MAX_ATTEMPTS + 1):
            last_result = self._run_cli(["auth", "check", "--test", "--passive"], timeout=AUTH_CHECK_TIMEOUT_SECONDS)
            if last_result["success"]:
                return last_result
            if attempt < AUTH_CHECK_MAX_ATTEMPTS:
                logger.warning(
                    f"Checagem de autenticação falhou (tentativa {attempt}/{AUTH_CHECK_MAX_ATTEMPTS}) - "
                    f"tentando de novo em {AUTH_CHECK_RETRY_WAIT_SECONDS}s (pode ser instabilidade de rede "
                    f"transitória, não sessão realmente expirada): {last_result['error']}"
                )
                time.sleep(AUTH_CHECK_RETRY_WAIT_SECONDS)
        return last_result

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

        Retorna {"success": True} só quando TODAS as fontes chegaram em 'ready' de
        verdade. Se alguma terminou em 'error' (bug real observado em produção: uma
        fonte de áudio entrou em erro terminal no backend do NotebookLM - "failed to
        process... never resolved" - e o código antigo tratava isso como "pronta",
        deixando o Estúdio ser gerado sem essa fonte), retorna {"success": False,
        "errored": [...]}. Se sobrou algo em 'preparing'/'processing' depois do
        timeout, retorna {"success": False, "pending": [...]} - quem chama decide o
        que fazer (nunca deve gerar o Estúdio em nenhum dos dois casos de False).

        Importante: o NotebookLM NUNCA substitui uma fonte com erro - cada tentativa
        de 'source add' (inclusive um retry manual bem-sucedido) cria uma linha NOVA
        na lista, deixando a tentativa antiga com status 'error' pra trás pra sempre
        (bug real observado: um áudio corrigido manualmente continuava bloqueando o
        Estúdio por causa de 3 tentativas antigas com erro do MESMO título). Por
        isso só conta como erro de verdade um TÍTULO que não tem nenhuma versão
        'ready' - se existe pelo menos uma versão pronta com aquele nome, as
        tentativas antigas com erro daquele mesmo título são ignoradas."""
        deadline = time.time() + timeout
        while True:
            result = self._run_cli(["source", "list", "-n", notebook_id], timeout=SOURCE_ADD_TIMEOUT_SECONDS)
            if not result["success"]:
                logger.warning(f"Não consegui checar o status das fontes do notebook {notebook_id}: {result['error']}")
                return {"success": False, "pending": [], "errored": [], "error": result["error"]}

            sources = (result["data"] or {}).get("sources", [])
            pending = [s for s in sources if s.get("status") in PENDING_SOURCE_STATUSES]
            ready_titles = {s["title"] for s in sources if s.get("status") == "ready" and s.get("title")}
            errored = [
                s for s in sources
                if s.get("status") == "error" and s.get("title") not in ready_titles
            ]

            if not pending:
                if errored:
                    errored_desc = [f"{s.get('title')} ({s.get('status')})" for s in errored]
                    logger.error(
                        f"{len(errored)} fonte(s) do notebook {notebook_id} terminaram em ERRO de "
                        f"processamento (não é 'ainda processando', é falha terminal do NotebookLM): "
                        f"{errored_desc} - NÃO vou gerar o Estúdio com fonte faltando."
                    )
                    return {"success": False, "pending": [], "errored": errored, "error": f"{len(errored)} fonte(s) com erro de processamento ({', '.join(errored_desc)})"}
                logger.info(f"Todas as fontes do notebook {notebook_id} estão prontas (ready).")
                return {"success": True, "pending": [], "errored": [], "error": None}

            if time.time() >= deadline:
                pending_desc = [f"{s.get('title')} ({s.get('status')})" for s in pending]
                logger.warning(
                    f"{len(pending)} fonte(s) do notebook {notebook_id} ainda não terminaram de "
                    f"processar após {timeout}s - provavelmente sobras de um 'source add' que falhou "
                    f"antes (ficam presas em 'preparing' pra sempre, por design da CLI): {pending_desc}"
                )
                return {"success": False, "pending": pending, "errored": [], "error": f"{len(pending)} fonte(s) presas em processamento"}

            logger.info(
                f"Aguardando {len(pending)} fonte(s) do notebook {notebook_id} terminarem de processar "
                f"antes de gerar o Estúdio (checando de novo em {SOURCES_READY_POLL_INTERVAL_SECONDS}s)..."
            )
            time.sleep(SOURCES_READY_POLL_INTERVAL_SECONDS)

    def cleanup_stuck_sources(self, notebook_id: str, pending_sources: List[Dict[str, Any]]) -> int:
        """Apaga fontes ainda presas (preparing/processing/unknown). NÃO é mais
        chamado automaticamente pelo orchestrator (era chamado a cada timeout de
        wait_for_sources_ready, mas isso apagava fontes que só precisavam de mais
        tempo pra indexar - ex.: áudio de aula real, não só "fantasmas" de um
        'source add' que falhou de verdade). Fica disponível pra limpeza manual,
        quando alguém confirmar de propósito que uma fonte específica está
        realmente presa pra sempre (não só lenta). Retorna quantas foram apagadas."""
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

    def delete_sources_by_title(self, notebook_id: str, title: str) -> int:
        """Apaga TODAS as fontes com esse título exato (qualquer status), pra poder
        subir uma versão nova no lugar - usado quando o CONTEÚDO de uma fonte já
        existente precisa ser trocado (ex.: transcrição regerada com um prompt
        corrigido), caso em que add_sources_to_notebook pularia por já achar o
        título "presente" (bug real: ela só verifica presença por nome, não
        conteúdo). Retorna quantas foram apagadas."""
        result = self._run_cli(["source", "list", "-n", notebook_id], timeout=SOURCE_ADD_TIMEOUT_SECONDS)
        if not result["success"]:
            logger.warning(f"Não consegui listar as fontes do notebook {notebook_id} pra apagar '{title}': {result['error']}")
            return 0
        sources = (result["data"] or {}).get("sources", [])
        matches = [s for s in sources if s.get("title") == title]
        return self.cleanup_stuck_sources(notebook_id, matches)

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

    def set_public_sharing(self, notebook_id: str) -> Dict[str, Any]:
        """Ativa o compartilhamento público por link ('qualquer um com o link pode
        ver', somente leitura) - sem isso, um notebook novo nasce privado por
        padrão e a turma não consegue abrir o link da planilha sem pedir acesso
        manualmente. Chamado uma vez, logo após criar o notebook.

        Também força o nível de acesso dos leitores para "todo o notebook"
        (fontes + Estúdio), não só o chat - por padrão, um notebook novo com
        link público habilitado deixa os leitores com acesso a "somente
        conversa" (`share view-level` default é "chat"), escondendo fontes e
        artefatos do Estúdio de quem abre o link. Bug real encontrado em
        produção (2026-08-26): a usuária reportou que o notebook da UC06 só
        mostrava o chat pra quem abria o link.

        Retorna {"success", "share_url", "error"} - o `share_url` retornado aqui
        (domínio notebook.google.com) é o link de verdade que funciona pra quem
        não é dono do notebook. Usar esse em vez de montar a URL manualmente:
        bug real encontrado em produção onde o domínio "notebooklm.google.com"
        (usado antes pra montar o link salvo na planilha) dava "Notebook não
        encontrado" pra quem abria o link, mesmo o notebook existindo e sendo
        público - o domínio certo é sem o "lm"."""
        result = self._run_cli(["share", "public", "-n", notebook_id, "--enable"], timeout=CREATE_TIMEOUT_SECONDS)
        if not result["success"]:
            logger.warning(f"Não consegui ativar o compartilhamento público do notebook {notebook_id}: {result['error']}")
            return {"success": False, "share_url": None, "error": result["error"]}
        share_url = (result["data"] or {}).get("share_url")

        view_level_result = self._run_cli(["share", "view-level", "full", "-n", notebook_id], timeout=CREATE_TIMEOUT_SECONDS)
        if not view_level_result["success"]:
            logger.warning(
                f"Compartilhamento público do notebook {notebook_id} ativado, mas não consegui liberar "
                f"acesso a 'todo o notebook' pros leitores (ficaram só com o chat): {view_level_result['error']}"
            )

        return {"success": True, "share_url": share_url, "error": None}

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
        # --mime-type explícito em vez de deixar a CLI adivinhar sozinha - mesma
        # cautela do upload pro Gemini (core/multimodal_processor.py): não dá pra
        # confiar que a detecção automática por extensão funciona igual em todo
        # ambiente (já vimos isso quebrar pra .pptx no container Linux).
        mime_type = guess_mime_type(file_path.name)
        add_cmd = ["source", "add", str(file_path.absolute()), "-n", notebook_id]
        if mime_type:
            add_cmd += ["--mime-type", mime_type]

        add_result = None
        for attempt in range(1, SOURCE_ADD_MAX_ATTEMPTS + 1):
            add_result = self._run_cli(add_cmd, timeout=SOURCE_ADD_TIMEOUT_SECONDS)
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

    def get_audio_source_guides(self, notebook_id: str, audio_titles: List[str]) -> List[str]:
        """Retorna o texto do 'Source Guide' (o resumo + keywords que o PRÓPRIO
        NotebookLM gera autonomamente pra cada fonte) das fontes de áudio da aula.

        Usado no caminho "áudio-primeiro" (orchestrator): quando o NotebookLM
        ingeriu o áudio direto (sem transcrição via Gemini - confirmado por teste
        em 2026-08-31 que o Pi, IP residencial, ingere áudio normalmente; o
        bloqueio de "IP de datacenter" era herança do Cloud Run), o guia dele vira
        o insumo de texto pro Claude gerar os flashcards (junto com os slides) -
        sem gastar NENHUMA chamada da cota do Gemini.

        Recebe os nomes/títulos das fontes de áudio e devolve só os summaries dos
        guias que existirem e estiverem prontos (vazio se nenhum). "Audio" aqui = a
        fonte cujo título é o nome do arquivo de áudio adicionado."""
        if not audio_titles:
            return []
        wanted = {t for t in audio_titles if t}
        result = self._run_cli(["source", "list", "-n", notebook_id], timeout=SOURCE_ADD_TIMEOUT_SECONDS)
        if not result["success"]:
            logger.warning(f"Não consegui listar fontes pra buscar os guias de áudio do notebook {notebook_id}: {result['error']}")
            return []
        summaries = []
        for s in (result["data"] or {}).get("sources", []):
            if s.get("status") != "ready" or s.get("title") not in wanted:
                continue
            g = self._run_cli(["source", "guide", s["id"], "-n", notebook_id], timeout=SOURCE_ADD_TIMEOUT_SECONDS)
            if g["success"] and (g["data"] or {}).get("summary"):
                summaries.append(g["data"]["summary"].strip())
            elif not g["success"]:
                logger.warning(f"Falha ao buscar o guide da fonte '{s.get('title')}': {g['error']}")
        return summaries

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
                ["generate", "mind-map", "-n", notebook_id, "--language", STUDIO_LANGUAGE],
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
