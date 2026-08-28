"""Sincronização AO VIVO com o Anki via AnkiConnect (addon que expõe uma API HTTP
local em http://localhost:8765) - só funciona rodando LOCAL (Windows) com o Anki
aberto e o addon AnkiConnect instalado; nunca funciona no Cloud Run (não tem como
o container alcançar o "localhost" da máquina da usuária).

Roda em PARALELO à geração do .apkg (core/anki_flashcards.py), não no lugar dela:
o .apkg continua sendo gerado e publicado sempre (é o que sustenta o pipeline
rodando no Cloud Run, onde o Anki não está acessível) - a sincronização via
AnkiConnect é um "bônus" quando disponível, pra não precisar importar manualmente
toda vez que o Anki já está aberto na hora do processamento.

Duplicidade: usa duplicateScope="deck" (só considera duplicada uma nota com o
mesmo campo-chave DENTRO do mesmo deck da aula) e NÃO sobrescreve notas já
existentes - uma nota "duplicada" (mesmo enunciado/assertiva já presente no
deck) é simplesmente pulada, nunca substituída. Isso significa que regerar os
flashcards com texto novo cria notas NOVAS ao lado das antigas (mesmo
comportamento/limitação do fluxo por .apkg) - apagar as antigas continua sendo
manual, no próprio Anki, quando a usuária quiser.
"""
import base64
import json
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib import request as urllib_request
from urllib.error import URLError

from config.settings import settings
from utils.logger import logger

ANKI_CONNECT_VERSION = 6
REQUEST_TIMEOUT_SECONDS = 60

MODEL_MULTIPLA_ESCOLHA = "Múltipla Escolha Universal"
MODEL_VERDADEIRO_FALSO = "Verdadeiro ou Falso Universal"

# Mesma ordem de campos de core/anki_flashcards.py - tem que bater com o note
# type real do Anki (nomes de campo, não só a ordem, importam pro AnkiConnect).
FIELDS_MULTIPLA_ESCOLHA = [
    "Enunciado", "Materia", "Imagem", "Imagem_Verso", "Resposta_Correta",
    "Opcao_2", "Opcao_3", "Opcao_4", "Opcao_5", "Opcao_6", "Opcao_7", "Opcao_8",
    "Pegadinha", "Explicação", "Fonte", "Video",
]
FIELDS_VERDADEIRO_FALSO = [
    "Assertiva", "Materia", "Contexto_Enunciado", "Imagem", "Gabarito",
    "Pegadinha", "Explicação", "Fonte", "Video",
]

TAG_PADRAO = "gerado-claude"


def _invoke(action: str, **params: Any) -> Dict[str, Any]:
    """Chamada crua ao AnkiConnect. Levanta exceção em qualquer falha de rede/
    protocolo - quem chama (is_available/sync_flashcards_to_anki) decide o que
    fazer, nunca deixa uma exceção daqui vazar pro resto do pipeline."""
    payload = json.dumps({"action": action, "version": ANKI_CONNECT_VERSION, "params": params}).encode("utf-8")
    req = urllib_request.Request(settings.secrets.ANKI_CONNECT_URL, data=payload)
    with urllib_request.urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    if body.get("error"):
        raise RuntimeError(body["error"])
    return body["result"]


# Hierarquia fixa pedida pela usuária: Medicina > Segundo Ano > <UC> > <aula>.
# Substituiu duas tentativas anteriores (primeiro "Medicina::<UC>::<aula>" sem
# ano, depois "casar com um deck já existente da usuária tipo Faculdade::2º
# Ano::UC05 - Respiratório") - manter só essa, sem lógica de auto-detecção, é
# o que a usuária pediu explicitamente por último.
DECK_ROOT = "Medicina"
DECK_ANO = "Segundo Ano"


def _resolve_deck_name(unit_code: str, lesson_name: str) -> str:
    """Monta "Medicina::Segundo Ano::<UC>::<aula>" - hierarquia fixa, sem
    depender de decks pré-existentes da usuária."""
    return f"{DECK_ROOT}::{DECK_ANO}::{unit_code}::{lesson_name}"


def is_available() -> bool:
    """Confere se o Anki está aberto com o addon AnkiConnect instalado e
    respondendo - checagem rápida e silenciosa (nunca loga erro, isso é
    esperado no Cloud Run e em qualquer execução sem o Anki aberto)."""
    try:
        _invoke("version")
        return True
    except (URLError, OSError, RuntimeError, TimeoutError):
        return False


def _upload_media(image_path: str, uploaded_cache: Dict[str, str]) -> Optional[str]:
    """Sobe `image_path` pro Anki via storeMediaFile (se ainda não subiu nesta
    sincronização) e devolve o filename a referenciar num `<img>`, ou None se o
    arquivo não existir mais. `uploaded_cache` (caminho local -> filename já
    enviado) evita subir a MESMA imagem de novo quando duas ou mais cards da
    mesma aula reaproveitam a mesma página do slide - sem isso, uma aula com
    muitas cards reaproveitando poucas imagens levaria muito mais tempo (e
    request maior) do que o necessário, arriscando timeout."""
    if image_path in uploaded_cache:
        return uploaded_cache[image_path]
    path = Path(image_path)
    if not path.exists():
        logger.warning(f"Imagem '{image_path}' referenciada no card não existe mais - card vai sem essa imagem.")
        return None
    data_b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    filename = path.name
    _invoke("storeMediaFile", filename=filename, data=data_b64)
    uploaded_cache[image_path] = filename
    return filename


def _image_field_html(card: Dict[str, Any], uploaded_cache: Dict[str, str]) -> str:
    """HTML `<img>` pro campo "Imagem" (mostrado do lado da PERGUNTA) - usado
    quando a própria pergunta depende de ver a imagem."""
    image_path = card.get("imagem_path")
    if not image_path:
        return ""
    filename = _upload_media(image_path, uploaded_cache)
    return f'<img src="{filename}">' if filename else ""


def _explicacao_com_imagem(card: Dict[str, Any], uploaded_cache: Dict[str, str]) -> str:
    """Texto do campo "Explicação" (o quadro "💡 GABARITO COMENTADO"), prefixado
    com a imagem do slide quando card["imagem_gabarito_path"] estiver setado -
    mesma lógica de core/anki_flashcards.py (ver lá o porquê de embutir a
    imagem no texto em vez de usar um campo "Imagem_Verso" dedicado: esse campo
    só existe no note type de múltipla escolha, não no de Verdadeiro/Falso)."""
    explicacao = card.get("explicacao", "")
    image_path = card.get("imagem_gabarito_path")
    if not image_path:
        return explicacao
    filename = _upload_media(image_path, uploaded_cache)
    if not filename:
        return explicacao
    return f'<img src="{filename}" style="margin-bottom:10px;">' + explicacao


def _mc_fields(card: Dict[str, Any], materia: str, uploaded_cache: Dict[str, str]) -> Dict[str, str]:
    opcoes_erradas = list(card.get("opcoes_erradas") or [])
    opcoes_padded = (opcoes_erradas + [""] * 7)[:7]
    values = [
        card.get("enunciado", ""),
        materia,
        _image_field_html(card, uploaded_cache),
        "",  # Imagem_Verso
        card.get("resposta_correta", ""),
        *opcoes_padded,
        card.get("pegadinha", ""),
        _explicacao_com_imagem(card, uploaded_cache),
        card.get("fonte", ""),
        card.get("video", ""),
    ]
    return dict(zip(FIELDS_MULTIPLA_ESCOLHA, values))


def _vf_fields(card: Dict[str, Any], materia: str, uploaded_cache: Dict[str, str]) -> Dict[str, str]:
    values = [
        card.get("assertiva", ""),
        materia,
        card.get("contexto_enunciado", ""),
        _image_field_html(card, uploaded_cache),
        card.get("gabarito", ""),
        card.get("pegadinha", ""),
        _explicacao_com_imagem(card, uploaded_cache),
        card.get("fonte", ""),
        card.get("video", ""),
    ]
    return dict(zip(FIELDS_VERDADEIRO_FALSO, values))


def import_apkg_package(apkg_path: Path) -> Dict[str, Any]:
    """Importa um .apkg já pronto direto pro Anki aberto na máquina, via
    AnkiConnect (ação 'importPackage') - usado por sync_cloud_flashcards_to_anki.py
    pra aulas processadas pelo Cloud Run, onde o AnkiConnect não estava
    acessível na hora do processamento (container não alcança o localhost da
    usuária). Ao contrário de sync_flashcards_to_anki (que reconstrói as notas
    campo a campo a partir do JSON gerado durante o processamento), a mídia
    (imagens dos slides) já vem embutida no próprio .apkg - não depende de
    nenhum caminho de arquivo temporário, que já não existe mais depois que o
    container do Cloud Run que gerou aquela aula terminou de rodar.

    Retorna {"success", "available", "error"}. `available=False` (sem erro)
    é o caso normal de Anki fechado - quem chama trata como "sincronização
    pulada por enquanto", não como falha."""
    if not is_available():
        return {"success": True, "available": False, "error": None}
    try:
        _invoke("importPackage", path=str(apkg_path.absolute()))
        logger.info(f"'.apkg' importado no Anki via AnkiConnect: {apkg_path.name}")
        return {"success": True, "available": True, "error": None}
    except Exception as e:
        logger.warning(f"Falha ao importar '.apkg' no Anki via AnkiConnect ({apkg_path.name}): {e}")
        return {"success": False, "available": True, "error": str(e)}


def sync_flashcards_to_anki(
    flashcards: List[Dict[str, Any]], unit_code: str, lesson_name: str
) -> Dict[str, Any]:
    """Sincroniza `flashcards` DIRETO pro Anki aberto na máquina, via AnkiConnect -
    cria o deck "Medicina::<UC>::<aula>" se não existir e adiciona uma nota por
    card (pulando duplicatas dentro do mesmo deck, nunca sobrescrevendo).

    Retorna {"success", "available", "added", "skipped_duplicate", "failed", "error"}.
    `available=False` significa que o Anki/AnkiConnect não está acessível agora -
    não é um erro, é o caso normal quando roda no Cloud Run ou com o Anki fechado;
    quem chama deve tratar isso como "sync pulado", não como falha do pipeline.
    """
    if not is_available():
        return {"success": True, "available": False, "added": 0, "skipped_duplicate": 0, "failed": 0, "error": None}

    deck_name = _resolve_deck_name(unit_code, lesson_name)
    try:
        _invoke("createDeck", deck=deck_name)

        notes = []
        uploaded_cache: Dict[str, str] = {}
        for card in flashcards:
            tipo = (card.get("tipo") or "").strip().lower()
            if tipo == "mc":
                model_name, fields = MODEL_MULTIPLA_ESCOLHA, _mc_fields(card, unit_code, uploaded_cache)
            elif tipo == "vf":
                model_name, fields = MODEL_VERDADEIRO_FALSO, _vf_fields(card, unit_code, uploaded_cache)
            else:
                continue
            notes.append({
                "deckName": deck_name,
                "modelName": model_name,
                "fields": fields,
                "tags": [TAG_PADRAO],
                "options": {"allowDuplicate": False, "duplicateScope": "deck"},
            })

        if not notes:
            return {"success": True, "available": True, "added": 0, "skipped_duplicate": 0, "failed": 0, "error": "nenhum flashcard válido"}

        # canAddNotes: confere ANTES de tentar adicionar, pra reportar corretamente
        # quantas são duplicatas em vez de contar como "falha".
        can_add = _invoke("canAddNotes", notes=notes)
        to_add = [n for n, ok in zip(notes, can_add) if ok]
        skipped = len(notes) - len(to_add)

        added = 0
        failed = 0
        if to_add:
            result_ids = _invoke("addNotes", notes=to_add)
            added = sum(1 for r in result_ids if r is not None)
            failed = sum(1 for r in result_ids if r is None)

        logger.info(
            f"Sincronizado com o Anki (AnkiConnect): deck '{deck_name}' - "
            f"{added} nota(s) nova(s), {skipped} duplicata(s) pulada(s), {failed} falha(s)."
        )
        return {"success": True, "available": True, "added": added, "skipped_duplicate": skipped, "failed": failed, "error": None}

    except Exception as e:
        logger.warning(f"Falha ao sincronizar com o Anki via AnkiConnect (deck '{deck_name}'): {e}")
        return {"success": False, "available": True, "added": 0, "skipped_duplicate": 0, "failed": 0, "error": str(e)}
