import os
import json
import json_repair
import random
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import List, Dict, Any, Optional

from google import genai
from google.genai import types
from google.genai.errors import ServerError, ClientError
from config.settings import settings
from core.file_sniff import guess_mime_type
from database.db import db_manager
from utils.logger import logger
from dotenv import load_dotenv

load_dotenv()

# Modelo principal e modelos de fallback (família Gemini 3.x), usados nessa ordem
# quando o principal esgota as tentativas por sobrecarga (503). Confirmados como
# disponíveis via client.models.list() em 2026-08-24.
PRIMARY_MODEL = "gemini-3.6-flash"
FALLBACK_MODELS = ["gemini-3.7-flash", "gemini-3.5-flash"]

# Reduzido de 5 pra 3 (com os 2 fallbacks, no máximo 3×3=9 chamadas reais por
# aula, em vez de até 15) - especificamente pra não deixar uma única aula com
# sobrecarga sustentada comer quase toda a cota diária do tier gratuito (20)
# sozinha. O backoff exponencial já cresce rápido (4s/8s/16s), então 3
# tentativas por modelo ainda cobre bem a maioria dos picos "temporários" de
# demanda que o próprio erro do Google descreve.
MAX_RETRIES_PER_MODEL = 3
BASE_BACKOFF_SECONDS = 4
MAX_BACKOFF_SECONDS = 60

# Cota diária do tier GRATUITO da API do Gemini (confirmada em produção via erro
# real: "generate_content_free_tier_requests, limit: 20"). Cada chamada de
# verdade (sucesso OU falha) conta contra essa cota - por isso é rastreada e
# checada ANTES de cada tentativa (database/db.py, tabela gemini_daily_usage),
# não só depois que a API já rejeitou.
GEMINI_FREE_TIER_DAILY_LIMIT = 20

FILE_ACTIVE_POLL_INTERVAL_SECONDS = 2
FILE_ACTIVE_POLL_TIMEOUT_SECONDS = 120

# Bitrate/canal alvo ao recomprimir áudio antes do upload (reduz payload = menor
# chance de 503 em arquivos pesados). Só é usado se o ffmpeg estiver instalado.
AUDIO_COMPRESSION_BITRATE = "64k"
AUDIO_COMPRESSION_TIMEOUT_SECONDS = 420

# Nome do arquivo de transcrição gerado por este módulo (salvo na própria pasta
# da aula). core/drive_sync.py importa essa constante pra EXCLUIR esse arquivo
# do escaneamento de materiais - sem isso, uma aula que ficou "partial_failure"
# numa tentativa anterior (já gerou a transcrição, mas falhou em outra etapa)
# seria reprocessada incluindo sua PRÓPRIA transcrição como se fosse material
# original, alimentando o Gemini/NotebookLM com o próprio output de uma rodada
# anterior. O orchestrator já tem seu próprio mecanismo deliberado pra adicionar
# esse arquivo como fonte extra (via o campo "transcript_path" do retorno de
# analyze_lesson_materials) - não precisa (e não deve) vir do scanner de pasta.
GENERATED_TRANSCRIPT_FILENAME = "transcricao_aula.txt"

# Mínimo de flashcards por aula - pedido explícito no prompt (abaixo) e reforçado
# aqui como rede de segurança: se o Gemini mesmo assim devolver menos que isso
# (acontece com aulas de conteúdo mais curto), _ensure_min_flashcards completa a
# diferença reaproveitando a mesma transcrição, sem precisar reprocessar áudio/slide.
MIN_FLASHCARDS_PER_LESSON = 20


class GeminiOverloadedError(Exception):
    """Levantado quando o modelo principal e todos os fallbacks esgotam as tentativas por 503."""


class GeminiDailyBudgetExceededError(Exception):
    """Levantado quando a cota diária do tier gratuito (GEMINI_FREE_TIER_DAILY_LIMIT)
    já foi atingida - para de tentar em vez de continuar gastando a cota do dia."""


class MultimodalProcessor:
    """Processa arquivos multimodais com retry + fallback de modelo em caso de picos de demanda (503)."""

    def __init__(self):
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            logger.error("ATENÇÃO: GEMINI_API_KEY não foi encontrada no arquivo .env ou no ambiente!")

        self.client = genai.Client(api_key=api_key) if api_key else genai.Client()
        self.model_name = PRIMARY_MODEL

        self._ffmpeg_path = shutil.which("ffmpeg")
        if not self._ffmpeg_path:
            logger.debug(
                "ffmpeg não encontrado no PATH; upload de áudio será feito sem recompressão prévia."
            )

    # ------------------------------------------------------------------
    # Upload e polling de estado dos arquivos no Gemini
    # ------------------------------------------------------------------

    def _wait_for_active(self, file_ref: Any) -> Any:
        """Aguarda o arquivo enviado sair do estado PROCESSING e chegar em ACTIVE
        antes de ser usado em generate_content (mesmo padrão de core/gemini_client.py)."""
        elapsed = 0
        while file_ref.state.name == "PROCESSING" and elapsed < FILE_ACTIVE_POLL_TIMEOUT_SECONDS:
            time.sleep(FILE_ACTIVE_POLL_INTERVAL_SECONDS)
            elapsed += FILE_ACTIVE_POLL_INTERVAL_SECONDS
            file_ref = self.client.files.get(name=file_ref.name)

        if file_ref.state.name == "PROCESSING":
            raise TimeoutError(
                f"Arquivo {file_ref.display_name} não ficou ACTIVE em "
                f"{FILE_ACTIVE_POLL_TIMEOUT_SECONDS}s (ainda PROCESSING)."
            )
        if file_ref.state.name == "FAILED":
            raise RuntimeError(f"Processamento do arquivo {file_ref.display_name} falhou no Gemini.")

        return file_ref

    @staticmethod
    def _parse_json_response(text: str, context: str) -> Any:
        """json.loads com strict=False (tolera controle não escapado em strings -
        comum em transcrições longas). Se mesmo assim falhar, loga um trecho do
        texto ao redor da posição exata do erro (senão um "Expecting ',' delimiter:
        line X column Y" genérico não dá nenhuma pista de causa) e tenta reparar
        com json_repair antes de desistir - causa real confirmada em produção:
        sem response_schema estruturado, o Gemini às vezes esquece de escapar
        aspas literais dentro de um valor string (ex.: transcrição de alguém
        lendo uma citação em voz alta: `"Rapidinho. "Este relato..."`), o que
        quebra o parser estrito do Python mesmo com strict=False."""
        try:
            return json.loads(text, strict=False)
        except json.JSONDecodeError as e:
            snippet_start = max(0, e.pos - 200)
            snippet_end = min(len(text), e.pos + 200)
            logger.warning(
                f"JSON malformado na resposta do Gemini ({context}): {e}. "
                f"Tamanho total da resposta: {len(text)} chars. "
                f"Trecho ao redor do erro: ...{text[snippet_start:snippet_end]!r}... "
                f"Tentando reparar com json_repair antes de desistir."
            )
            try:
                repaired = json_repair.loads(text)
            except Exception as repair_err:
                logger.error(f"json_repair também falhou ({context}): {repair_err}")
                raise e
            if not repaired:
                logger.error(f"json_repair não conseguiu recuperar nada útil ({context}) - resposta provavelmente truncada.")
                raise e
            logger.warning(f"JSON reparado com sucesso via json_repair ({context}) - resultado pode estar incompleto, revisar se possível.")
            return repaired

    def _upload_and_wait(self, path: Path, uploaded_files: list) -> Any:
        """Sobe `path` pro Gemini. Se o nome do arquivo tiver caractere não-ASCII
        (comum: nome de aula com acento, ex.: "Herança"), sobe uma CÓPIA com nome
        sanitizado em vez do arquivo original - bug real visto em produção: o SDK
        do Gemini quebra com 'ascii codec can't encode character' ao montar a
        requisição de upload com um nome de arquivo acentuado (não é bug de
        locale/UTF-8 do sistema - persiste mesmo com o container em UTF-8 e o
        nome já normalizado; é o próprio SDK/HTTP tentando tratar o nome como
        ASCII). Preserva a extensão, só troca os caracteres do nome em si."""
        upload_path = path
        temp_ascii_copy: Optional[Path] = None
        try:
            path.name.encode("ascii")
        except UnicodeEncodeError:
            temp_ascii_copy = Path(tempfile.gettempdir()) / f"gemini_upload_{abs(hash(str(path)))}{path.suffix}"
            shutil.copyfile(path, temp_ascii_copy)
            upload_path = temp_ascii_copy
            logger.debug(f"Nome de arquivo com acento ('{path.name}') - subindo cópia sanitizada pro Gemini: {temp_ascii_copy.name}")

        try:
            # mime_type explícito em vez de deixar o SDK adivinhar pela extensão via
            # mimetypes.guess_type() do sistema - bug real visto em produção: no
            # container Linux isso retorna None pra .pptx/.docx/etc (funciona no
            # Windows local, onde foi testado, mas falha lá com "Unknown mime type").
            mime_type = guess_mime_type(upload_path.name)
            upload_config = types.UploadFileConfig(mime_type=mime_type) if mime_type else None
            f_ref = self.client.files.upload(file=str(upload_path), config=upload_config)
            uploaded_files.append(f_ref)
            return self._wait_for_active(f_ref)
        finally:
            if temp_ascii_copy is not None:
                temp_ascii_copy.unlink(missing_ok=True)

    # ------------------------------------------------------------------
    # Compressão opcional de áudio (reduz payload antes do upload)
    # ------------------------------------------------------------------

    def _maybe_compress_audio(self, audio_path: Path) -> Path:
        """Tenta reencodar o áudio para mono/bitrate reduzido via ffmpeg antes do upload,
        como estratégia adicional para reduzir a chance de 503 em arquivos pesados.
        Se o ffmpeg não estiver disponível ou a conversão falhar por qualquer motivo,
        cai silenciosamente de volta para o arquivo original (nunca quebra o pipeline)."""
        if not self._ffmpeg_path:
            return audio_path

        try:
            tmp_dir = Path(tempfile.gettempdir()) / "med_study_automator_audio"
            tmp_dir.mkdir(parents=True, exist_ok=True)
            out_path = tmp_dir / f"{audio_path.stem}_compressed.mp3"

            cmd = [
                self._ffmpeg_path, "-y", "-i", str(audio_path),
                "-ac", "1", "-b:a", AUDIO_COMPRESSION_BITRATE,
                str(out_path),
            ]
            result = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=AUDIO_COMPRESSION_TIMEOUT_SECONDS,
            )

            if result.returncode != 0:
                logger.warning(
                    f"Falha ao recomprimir áudio {audio_path.name} com ffmpeg (usando original). "
                    f"stderr: {result.stderr[-500:] if result.stderr else '(vazio)'}"
                )
                return audio_path

            if out_path.exists() and out_path.stat().st_size > 0:
                logger.info(
                    f"Áudio recomprimido para upload: {audio_path.name} "
                    f"({audio_path.stat().st_size} -> {out_path.stat().st_size} bytes)"
                )
                return out_path

        except Exception as e:
            logger.warning(
                f"Erro inesperado ao recomprimir áudio {audio_path.name}, usando original: {e}"
            )

        return audio_path

    # ------------------------------------------------------------------
    # Chamada ao Gemini com retry (backoff exponencial + jitter) e fallback de modelo
    # ------------------------------------------------------------------

    @staticmethod
    def _is_overload_error(err: Exception) -> bool:
        """Detecta especificamente erros de sobrecarga/503 do Gemini, para não mascarar
        outros bugs (auth, payload inválido, etc.) atrás de um retry genérico."""
        if isinstance(err, ServerError):
            code = getattr(err, "code", None)
            if code == 503:
                return True
            status = (getattr(err, "status", "") or "").upper()
            if status == "UNAVAILABLE":
                return True
            msg = str(err).lower()
            return "503" in msg or "unavailable" in msg or "overloaded" in msg or "high demand" in msg
        return False

    @staticmethod
    def _is_model_exhausted_error(err: Exception) -> bool:
        """Detecta um 429 RESOURCE_EXHAUSTED de cota (ex.: limite diário do tier
        gratuito para ESSE modelo). Retry no mesmo modelo não adianta nada (a cota só
        libera depois de um tempo/no dia seguinte), mas como a cota é por modelo,
        trocar de modelo imediatamente costuma resolver - sem gastar tentativas com
        backoff à toa."""
        if isinstance(err, ClientError):
            code = getattr(err, "code", None)
            if code == 429:
                return True
            status = (getattr(err, "status", "") or "").upper()
            if status == "RESOURCE_EXHAUSTED":
                return True
            msg = str(err).lower()
            return "429" in msg or "resource_exhausted" in msg or "quota" in msg
        return False

    def _generate_with_retry(self, contents: list) -> Any:
        """Tenta gerar conteúdo no modelo principal com backoff exponencial + jitter;
        se esgotar as tentativas por sobrecarga (503) OU esbarrar numa cota esgotada
        (429), cai para os modelos de fallback da família Gemini 3.x antes de desistir
        de vez. Cota esgotada pula direto pro próximo modelo, sem gastar tentativas
        de retry no mesmo modelo (a cota é por modelo, então insistir não ajuda).

        Antes de CADA tentativa de verdade, checa o orçamento diário do tier
        gratuito (GEMINI_FREE_TIER_DAILY_LIMIT) - se já foi atingido, para na
        hora em vez de continuar gastando o resto da cota do dia numa aula só
        que já está com sobrecarga sustentada."""
        models_to_try = [self.model_name] + [m for m in FALLBACK_MODELS if m != self.model_name]
        last_error: Optional[Exception] = None

        for model in models_to_try:
            for attempt in range(1, MAX_RETRIES_PER_MODEL + 1):
                used_today = db_manager.get_gemini_request_count_today()
                if used_today >= GEMINI_FREE_TIER_DAILY_LIMIT:
                    raise GeminiDailyBudgetExceededError(
                        f"Orçamento diário do Gemini (tier gratuito, {GEMINI_FREE_TIER_DAILY_LIMIT}/dia) "
                        f"já foi atingido ({used_today} requisições hoje) - parando antes de gastar mais "
                        f"cota. Tenta de novo amanhã (ou ative o tier pago pra não ter esse limite)."
                    )
                try:
                    request_number_today = db_manager.increment_gemini_request_count()
                    logger.info(
                        f"Enviando requisição multimodal para o modelo {model} "
                        f"(tentativa {attempt}/{MAX_RETRIES_PER_MODEL}; requisição "
                        f"{request_number_today}/{GEMINI_FREE_TIER_DAILY_LIMIT} do dia)..."
                    )
                    response = self.client.models.generate_content(
                        model=model,
                        contents=contents,
                        config=types.GenerateContentConfig(
                            temperature=0.2,
                            response_mime_type="application/json",
                        ),
                    )
                    if model != self.model_name:
                        logger.warning(
                            f"Resposta obtida usando modelo de fallback '{model}' "
                            f"(principal '{self.model_name}' não disponível no momento)."
                        )
                    return response

                except Exception as api_err:
                    last_error = api_err

                    if self._is_model_exhausted_error(api_err):
                        logger.warning(
                            f"Modelo {model} com cota esgotada (429) - pulando direto para o "
                            f"próximo modelo, sem gastar retries neste."
                        )
                        break  # tenta o próximo modelo de fallback imediatamente

                    if not self._is_overload_error(api_err):
                        # Erro que não é sobrecarga nem cota (auth, payload, etc.): retry
                        # não ajuda e trocar de modelo também não resolveria - propaga já.
                        logger.error(f"Erro não relacionado a sobrecarga/cota no modelo {model}: {api_err}")
                        raise

                    if attempt < MAX_RETRIES_PER_MODEL:
                        backoff = min(BASE_BACKOFF_SECONDS * (2 ** (attempt - 1)), MAX_BACKOFF_SECONDS)
                        backoff += random.uniform(0, backoff * 0.25)
                        logger.warning(
                            f"Modelo {model} com alta demanda (503) - tentativa {attempt}/"
                            f"{MAX_RETRIES_PER_MODEL}, aguardando {backoff:.1f}s..."
                        )
                        time.sleep(backoff)
                    else:
                        logger.warning(f"Modelo {model} esgotou as {MAX_RETRIES_PER_MODEL} tentativas por sobrecarga.")

        raise GeminiOverloadedError(
            f"Todos os modelos ({', '.join(models_to_try)}) falharam por sobrecarga/cota "
            f"esgotada após até {MAX_RETRIES_PER_MODEL} tentativas cada. Último erro: {last_error}"
        )

    # ------------------------------------------------------------------
    # Busca de vídeo real do YouTube (grounding via Google Search)
    # ------------------------------------------------------------------

    def _search_youtube_video_id(self, topic: str) -> Optional[str]:
        """Busca (de verdade, via Google Search grounding) um vídeo do YouTube
        relevante ao tópico. Só aceita um ID que apareça nos resultados de busca
        REAIS que o Gemini usou (grounding_chunks) - nunca um ID que o modelo só
        tenha citado de memória, pra não arriscar alucinação. Retorna None se não
        achar nada confiável (não é erro - só significa "sem vídeo dessa vez").

        Tenta os mesmos modelos de fallback da chamada principal (sem o retry/backoff
        completo - busca de vídeo é um "bônus", não crítico) porque cota esgotada
        (429) por modelo já se mostrou comum na prática."""
        models_to_try = [self.model_name] + [m for m in FALLBACK_MODELS if m != self.model_name]
        for model in models_to_try:
            try:
                response = self.client.models.generate_content(
                    model=model,
                    contents=(
                        f"Busque no YouTube um vídeo educacional em português sobre: {topic} "
                        f"(contexto: medicina/aula de graduação). Responda só com o ID do vídeo "
                        f"(os 11 caracteres depois de 'v=' ou 'youtu.be/'), nada mais. Se não achar "
                        f"um vídeo claramente relevante, responda apenas SEM_VIDEO."
                    ),
                    config=types.GenerateContentConfig(
                        temperature=0.0,
                        tools=[types.Tool(google_search=types.GoogleSearch())],
                    ),
                )
                candidate = response.candidates[0] if response.candidates else None
                grounding_chunks = (
                    getattr(candidate.grounding_metadata, "grounding_chunks", None)
                    if candidate and getattr(candidate, "grounding_metadata", None)
                    else None
                )
                # Sem grounding_chunks = a busca não trouxe nada de verdade pra se
                # basear - não confia no texto sozinho, por mais que pareça um ID válido.
                if not grounding_chunks:
                    return None

                searched_youtube = any(
                    getattr(getattr(chunk, "web", None), "uri", "") and "youtu" in chunk.web.uri.lower()
                    for chunk in grounding_chunks
                )
                if not searched_youtube:
                    return None

                text = (response.text or "").strip()
                if not text or "SEM_VIDEO" in text.upper():
                    return None
                video_id = text.split()[0].strip().strip('"').strip("'")
                if len(video_id) == 11 and all(c.isalnum() or c in "-_" for c in video_id):
                    return video_id
                return None
            except Exception as e:
                if self._is_model_exhausted_error(e) or self._is_overload_error(e):
                    logger.debug(f"Modelo {model} indisponível pra busca de vídeo ('{topic}'), tentando próximo: {e}")
                    continue
                logger.debug(f"Busca de vídeo para '{topic}' falhou (sem problema, fica em branco): {e}")
                return None
        return None

    def _pick_slide_for_card(self, card: Dict[str, Any], slide_paths: List[Path]) -> Optional[Path]:
        """Decide de qual PDF de slide (quando a aula tem mais de um) tirar a imagem
        de um card - casa o nome do arquivo citado em "fonte" com os slides
        realmente disponíveis; sem match claro (ou só 1 slide na aula, caso comum),
        cai no primeiro."""
        if not slide_paths:
            return None
        if len(slide_paths) == 1:
            return slide_paths[0]
        fonte = (card.get("fonte") or "").lower()
        for sp in slide_paths:
            if sp.name.lower() in fonte:
                return sp
        return slide_paths[0]

    def _attach_slide_images(
        self, flashcards: List[Dict[str, Any]], slide_paths: List[Path], lesson_name: str
    ) -> None:
        """Renderiza (sob demanda, só as páginas realmente referenciadas) as imagens de
        slide indicadas pelo Gemini e anexa os caminhos locais:
          - "imagem_slide_pagina" -> card["imagem_path"] (lado da PERGUNTA - core/anki_flashcards.py
            embute no campo "Imagem"; o prompt já instrui o Gemini a nunca apontar uma página
            que entregue a resposta aqui, mas resolve os dois campos do mesmo jeito).
          - "imagem_slide_pagina_gabarito" -> card["imagem_gabarito_path"] (lado da EXPLICAÇÃO -
            core/anki_flashcards.py embute no início do campo "Explicação"; aqui uma imagem
            rotulada/reveladora é o comportamento desejado).

        Nunca derruba o pipeline por causa disso: número de página inválido ou
        qualquer erro de renderização só loga um aviso e deixa o card sem essa imagem."""
        if not slide_paths:
            return
        from core.slide_extractor import slide_extractor  # import tardio: evita custo de import do pymupdf quando não há slide

        for card in flashcards:
            for page_field, target_field in (
                ("imagem_slide_pagina", "imagem_path"),
                ("imagem_slide_pagina_gabarito", "imagem_gabarito_path"),
            ):
                page = card.get(page_field)
                if not page:
                    continue
                try:
                    page = int(page)
                except (TypeError, ValueError):
                    continue
                slide = self._pick_slide_for_card(card, slide_paths)
                if not slide:
                    continue
                try:
                    image_path = slide_extractor.extract_single_page(slide, page, lesson_name)
                    card[target_field] = str(image_path)
                except Exception as e:
                    logger.warning(
                        f"Não consegui extrair a página {page} de '{slide.name}' pro card "
                        f"'{(card.get('enunciado') or card.get('assertiva') or '')[:60]}' ({page_field}): {e}"
                    )

    def _top_up_flashcards(
        self, flashcards: List[Dict[str, Any]], transcript: Optional[str], unit_code: str, lesson_name: str
    ) -> List[Dict[str, Any]]:
        """Completa `flashcards` até MIN_FLASHCARDS_PER_LESSON pedindo os que faltam
        ao Gemini com base na MESMA transcrição já obtida (sem reprocessar áudio/slide -
        bem mais barato). Rede de segurança pro pedido do prompt principal, pra casos
        de aula com conteúdo mais curto onde o modelo devolveu menos que o mínimo.
        Nunca derruba o pipeline: se a chamada extra falhar, devolve os flashcards
        originais mesmo (loga um aviso)."""
        missing = MIN_FLASHCARDS_PER_LESSON - len(flashcards)
        if missing <= 0 or not transcript:
            return flashcards

        logger.info(
            f"Gemini devolveu só {len(flashcards)} flashcard(s) (mínimo é {MIN_FLASHCARDS_PER_LESSON}) - "
            f"pedindo mais {missing} com base na transcrição já obtida..."
        )
        existing_questions = [
            (c.get("enunciado") or c.get("assertiva") or "").strip()
            for c in flashcards
            if (c.get("enunciado") or c.get("assertiva"))
        ]
        existing_block = "\n".join(f"- {q}" for q in existing_questions) or "(nenhum ainda)"

        prompt = f"""
        Você é um médico especialista e professor sênior.
        Gere EXATAMENTE {missing} flashcards NOVOS de alto rendimento para a aula
        '{lesson_name}' da unidade '{unit_code}', com base na transcrição abaixo.

        Os flashcards a seguir JÁ EXISTEM para esta aula - NÃO repita as mesmas
        perguntas nem variações óbvias delas; cubra ângulos/tópicos diferentes:
        {existing_block}

        Mesmo formato de campos de "tipo" mc/vf já usado antes ("topico_busca",
        "enunciado"/"assertiva", "resposta_correta"/"gabarito", "opcoes_erradas"
        quando mc, "contexto_enunciado" quando vf, "pegadinha", "explicacao"
        começando com "💡 GABARITO COMENTADO: [TÓPICO]", "fonte" indicando que veio
        da transcrição, ex.: "Transcrição da aula, {lesson_name}").

        Retorne EXATAMENTE um JSON válido no formato {{"flashcards": [...]}} e nada mais.

        Transcrição da aula:
        ---
        {transcript}
        ---
        """
        try:
            response = self._generate_with_retry([prompt])
            result_json = self._parse_json_response(response.text, "_ensure_min_flashcards")
            new_flashcards = result_json.get("flashcards") or []
            if new_flashcards:
                logger.info(f"{len(new_flashcards)} flashcard(s) adicional(is) gerado(s) para completar o mínimo.")
                self._enrich_flashcards_with_videos(new_flashcards)
                return flashcards + new_flashcards
            logger.warning("Pedido de flashcards adicionais não retornou nenhum card novo.")
        except Exception as e:
            logger.warning(f"Falha ao pedir flashcards adicionais pra completar o mínimo (seguindo com {len(flashcards)}): {e}")
        return flashcards

    def _enrich_flashcards_with_videos(self, flashcards: List[Dict[str, Any]]) -> None:
        """Preenche o campo 'video' de cada flashcard com um ID real do YouTube,
        um por tópico único (não um por card, pra não multiplicar chamadas à toa
        quando vários cards compartilham o mesmo assunto). Falha em achar vídeo
        pra um tópico não é erro - só deixa 'video' em branco pra aquele card."""
        topic_to_video: Dict[str, Optional[str]] = {}
        for card in flashcards:
            topic = (card.get("topico_busca") or "").strip()
            if not topic:
                card["video"] = ""
                continue
            if topic not in topic_to_video:
                topic_to_video[topic] = self._search_youtube_video_id(topic)
                logger.info(
                    f"Busca de vídeo para '{topic}': "
                    f"{'achou ' + topic_to_video[topic] if topic_to_video[topic] else 'nada confiável, deixando em branco'}"
                )
            card["video"] = topic_to_video[topic] or ""

    # ------------------------------------------------------------------
    # Gerar flashcards ADICIONAIS pra uma aula já processada (sob demanda)
    # ------------------------------------------------------------------

    def generate_more_flashcards(
        self, lesson_folder: Path, unit_code: str, lesson_name: str, quantity: int = 10
    ) -> Dict[str, Any]:
        """Gera `quantity` flashcards NOVOS pra uma aula já processada antes, sem
        reenviar slide/áudio pro Gemini de novo - reaproveita a transcrição salva
        em `transcricao_aula.txt` (GENERATED_TRANSCRIPT_FILENAME, bem mais barato que reprocessar o áudio).

        Evita repetir pergunta já feita passando os enunciados/assertivas já
        existentes (lidos de `flashcards.json`, salvo por analyze_lesson_materials)
        como contexto de "não repita isso". Acrescenta os novos ao mesmo
        `flashcards.json` (não sobrescreve os antigos).

        Retorna {"success", "new_flashcards", "all_flashcards", "error"}."""
        transcript_path = lesson_folder / GENERATED_TRANSCRIPT_FILENAME
        if not transcript_path.exists():
            return {
                "success": False, "new_flashcards": [], "all_flashcards": [],
                "error": (
                    "transcrição não encontrada nesta pasta - essa aula precisa ter sido "
                    "processada pelo pipeline normal (com áudio) antes de gerar mais flashcards"
                ),
            }
        transcript = transcript_path.read_text(encoding="utf-8")

        flashcards_json_path = lesson_folder / "flashcards.json"
        existing_flashcards: List[Dict[str, Any]] = []
        if flashcards_json_path.exists():
            try:
                existing_flashcards = json.loads(flashcards_json_path.read_text(encoding="utf-8"))
            except Exception as e:
                logger.warning(f"flashcards.json existente em {lesson_folder} não pôde ser lido ({e}) - seguindo sem contexto de duplicidade.")

        existing_questions = [
            (c.get("enunciado") or c.get("assertiva") or "").strip()
            for c in existing_flashcards
            if (c.get("enunciado") or c.get("assertiva"))
        ]
        existing_block = "\n".join(f"- {q}" for q in existing_questions) or "(nenhum flashcard existe ainda para esta aula)"

        prompt = f"""
        Você é um médico especialista e professor sênior.
        Gere EXATAMENTE {quantity} flashcards NOVOS de alto rendimento para a aula
        '{lesson_name}' da unidade '{unit_code}', com base na transcrição fornecida
        abaixo (fisiopatologia, critérios diagnósticos, farmacologia, conduta -
        evite perguntas triviais/genéricas).

        Os flashcards a seguir JÁ EXISTEM para esta aula - NÃO repita as mesmas
        perguntas nem variações óbvias delas; cubra ângulos/tópicos diferentes:
        {existing_block}

        Cada item deve ter "tipo": "mc" (múltipla escolha) ou "tipo": "vf"
        (verdadeiro ou falso), com os campos:

        Para "tipo": "mc":
          - "topico_busca": tópico curto (3-8 palavras) pra buscar um vídeo do
            YouTube relacionado a ESSE card especificamente.
          - "enunciado": a pergunta/vinheta clínica.
          - "resposta_correta": a alternativa certa.
          - "opcoes_erradas": lista com 2 a 7 alternativas erradas plausíveis.
          - "pegadinha": SE alguma alternativa errada for particularmente
            tentadora, explique em 1-2 frases por que ela está errada; senão ""
          - "explicacao": comece EXATAMENTE com "💡 GABARITO COMENTADO: [TÓPICO]"
            (troque [TÓPICO] pelo tema real), depois explique o raciocínio.
          - "fonte": indique que veio da transcrição da aula (ex.: "Transcrição
            da aula, {lesson_name}").

        Para "tipo": "vf":
          - "topico_busca": igual acima.
          - "contexto_enunciado": frase curta de contexto (pode ser "").
          - "assertiva": a afirmação a ser julgada.
          - "gabarito": EXATAMENTE "Verdadeiro" ou "Falso".
          - "pegadinha": igual acima.
          - "explicacao": igual acima, começando com "💡 GABARITO COMENTADO: [TÓPICO]".
          - "fonte": igual acima.

        Retorne EXATAMENTE um JSON válido no formato {{"flashcards": [...]}} e nada mais.

        Transcrição da aula:
        ---
        {transcript}
        ---
        """

        try:
            response = self._generate_with_retry([prompt])
            result_json = self._parse_json_response(response.text, "generate_more_flashcards")
            new_flashcards = result_json.get("flashcards") or []

            if not new_flashcards:
                return {"success": False, "new_flashcards": [], "all_flashcards": existing_flashcards, "error": "Gemini não retornou nenhum flashcard novo"}

            self._enrich_flashcards_with_videos(new_flashcards)

            all_flashcards = existing_flashcards + new_flashcards
            flashcards_json_path.write_text(
                json.dumps(all_flashcards, ensure_ascii=False, indent=2), encoding="utf-8"
            )

            return {"success": True, "new_flashcards": new_flashcards, "all_flashcards": all_flashcards, "error": None}

        except Exception as e:
            logger.error(f"Erro ao gerar flashcards adicionais para '{lesson_name}': {e}")
            return {"success": False, "new_flashcards": [], "all_flashcards": existing_flashcards, "error": str(e)}

    # ------------------------------------------------------------------
    # Ponto de entrada público
    # ------------------------------------------------------------------

    def analyze_lesson_materials(
        self,
        slide_paths: List[Path],
        audio_paths: List[Path],
        lesson_name: str,
        unit_code: str,
    ) -> Dict[str, Any]:
        """Envia slides e áudios para o Gemini com retry + fallback de modelo para erros 503.

        Retorna SEMPRE um dict com a chave "success" (bool), para que quem chamar saiba
        com certeza se a etapa deu certo. Quando success=False, "error" traz o motivo e
        "transcript"/"summary"/"flashcards" vêm None/[] - nunca devem ser tratados como
        resultado válido.
        """
        uploaded_files: list = []
        compressed_audio_paths: list = []
        try:
            slide_names = ", ".join(sp.name for sp in slide_paths if sp and sp.exists()) or "(nenhum slide)"

            prompt = f"""
            Você é um médico especialista e professor sênior.
            Analise rigorosamente os materiais fornecidos (slides e áudio) para a aula '{lesson_name}' da unidade '{unit_code}'.
            Os arquivos de slide se chamam: {slide_names}

            Sua tarefa é retornar estritamente um JSON contendo:
            1. "tema": O título/tema EXATO da aula, tal como está escrito no próprio slide
               (geralmente na primeira página/capa do PDF). Copie o texto literal do título -
               NÃO invente, NÃO resuma, NÃO parafraseie. Se não conseguir identificar um
               título claro no slide, retorne uma string vazia "".
            2. "transcript": Transcrição LITERAL (ipsis litteris) de TUDO que é dito no áudio,
               do início ao fim - não é um resumo nem uma reescrita em texto corrido acadêmico.
               Mantenha a fala tal como foi dita (incluindo repetições, hesitações e o jeito
               coloquial de falar do professor), na ORDEM em que aparece no áudio, sem pular
               nem condensar trechos. A ÚNICA correção permitida é a de erros de transcrição
               (ex.: um termo médico que o reconhecimento de voz claramente entendeu errado por
               soar parecido) - nunca reescreva, resuma, reorganize ou "limpe" o estilo da fala.
            3. "summary": Um resumo clínico estruturado, focado em fisiopatologia, diagnóstico, conduta e pérolas clínicas.
            4. "flashcards": uma lista de flashcards de alto rendimento cobrindo os pontos-chave
               da aula (fisiopatologia, critérios diagnósticos, farmacologia, conduta - evite
               perguntas triviais/genéricas). Gere PELO MENOS {MIN_FLASHCARDS_PER_LESSON}
               flashcards ao todo (pode gerar mais se o conteúdo real da aula sustentar - nunca
               menos que isso, mesmo que precise cobrir o mesmo tópico por ângulos diferentes:
               definição, mecanismo, diagnóstico diferencial, conduta, exceção/pegadinha
               clássica). Cada item deve ter "tipo": "mc" (múltipla escolha) ou "tipo": "vf"
               (verdadeiro ou falso), com os campos:

               Para "tipo": "mc":
                 - "topico_busca": tópico curto (3-8 palavras) pra buscar um vídeo do YouTube
                   relacionado a ESSE card especificamente (ex.: "Pneumonia bacteriana tratamento").
                 - "enunciado": a pergunta/vinheta clínica.
                 - "resposta_correta": a alternativa certa.
                 - "opcoes_erradas": lista com 2 a 7 alternativas erradas plausíveis.
                 - "pegadinha": SE alguma alternativa errada for particularmente tentadora,
                   explique em 1-2 frases por que ela está errada; senão "" (string vazia).
                 - "explicacao": comece EXATAMENTE com "💡 GABARITO COMENTADO: [TÓPICO]" (troque
                   [TÓPICO] pelo tema real), depois explique o raciocínio da resposta certa.
                 - "fonte": nome exato do arquivo de slide (um dos listados acima) + número da
                   página/slide de onde a informação foi tirada (ex.: "{slide_names}, slide 6").
                 - "imagem_slide_pagina": imagem mostrada do lado da PERGUNTA (antes de o aluno
                   responder) - use SOMENTE quando a imagem for necessária pra formular ou
                   entender a pergunta em si (ex.: "observe a imagem abaixo e identifique X"),
                   E ela NÃO contiver nada que entregue a resposta. REGRA CRÍTICA, NÃO
                   NEGOCIÁVEL: NUNCA aponte uma página cuja imagem tenha legenda, rótulo, seta
                   ou texto que já nomeie/identifique a estrutura, resposta ou conceito que o
                   card está perguntando - isso entrega a resposta antes de o aluno tentar
                   responder, o que invalida o card inteiro. A grande maioria dos diagramas
                   anatômicos rotulados (átrio, válvula, camada, estrutura já nomeada na
                   imagem) SE ENCAIXA NESSA PROIBIÇÃO e não deve ir aqui. NUNCA use uma página
                   de um documento tipo "roteiro com gabarito" ou similar (o próprio nome já
                   avisa que tem resposta). Na dúvida, deixe null - é preferível um card sem
                   imagem a um card que entrega a resposta. Número da página (inteiro, 1-based,
                   igual ao de "fonte") ou null.
                 - "imagem_slide_pagina_gabarito": imagem mostrada do lado da EXPLICAÇÃO (depois
                   de o aluno já ter respondido) - aqui é o lugar certo pra imagens rotuladas,
                   diagramas com a estrutura identificada, tabelas com a resposta, etc. (o
                   oposto da regra acima: aqui QUANTO MAIS a imagem esclarecer/confirmar a
                   resposta, melhor - ajuda a fixar o conteúdo). Use sempre que houver uma
                   imagem no slide que reforce visualmente a resposta certa, mesmo que
                   "imagem_slide_pagina" também esteja preenchido com uma página diferente.
                   Número da página (inteiro, 1-based) ou null se não houver imagem relevante.

               Para "tipo": "vf":
                 - "topico_busca": igual acima.
                 - "contexto_enunciado": frase curta de contexto (pode ser "").
                 - "assertiva": a afirmação a ser julgada.
                 - "gabarito": EXATAMENTE "Verdadeiro" ou "Falso".
                 - "pegadinha": igual acima.
                 - "explicacao": igual acima, começando com "💡 GABARITO COMENTADO: [TÓPICO]".
                 - "fonte": igual acima.
                 - "imagem_slide_pagina": igual acima.
                 - "imagem_slide_pagina_gabarito": igual acima.

            Retorne EXATAMENTE um JSON válido no seguinte formato e nada mais:
            {{
              "tema": "Título exato copiado do slide...",
              "transcript": "Transcrição literal do áudio, do início ao fim...",
              "summary": "Resumo clínico detalhado...",
              "flashcards": [
                {{"tipo": "mc", "topico_busca": "...", "enunciado": "...", "resposta_correta": "...", "opcoes_erradas": ["...", "..."], "pegadinha": "", "explicacao": "💡 GABARITO COMENTADO: ...", "fonte": "...", "imagem_slide_pagina": null, "imagem_slide_pagina_gabarito": null}},
                {{"tipo": "vf", "topico_busca": "...", "contexto_enunciado": "...", "assertiva": "...", "gabarito": "Verdadeiro", "pegadinha": "", "explicacao": "💡 GABARITO COMENTADO: ...", "fonte": "...", "imagem_slide_pagina": null, "imagem_slide_pagina_gabarito": null}}
              ]
            }}
            """

            contents = [prompt]
            for sp in slide_paths:
                if sp and sp.exists():
                    logger.info(f"Fazendo upload do slide para o Gemini: {sp.name}")
                    f_ref = self._upload_and_wait(sp, uploaded_files)
                    contents.append(f_ref)

            for ap in audio_paths:
                if ap and ap.exists():
                    upload_source = self._maybe_compress_audio(ap)
                    if upload_source != ap:
                        compressed_audio_paths.append(upload_source)
                    logger.info(f"Fazendo upload do áudio para o Gemini: {ap.name}")
                    f_ref = self._upload_and_wait(upload_source, uploaded_files)
                    contents.append(f_ref)

            response = self._generate_with_retry(contents)

            logger.info("Resposta bruta do Gemini recebida com sucesso.")
            result_json = self._parse_json_response(response.text, "analyze_lesson_materials")

            # Salva automaticamente a transcrição como arquivo .txt na pasta da aula
            # (pasta do áudio, ou do slide se não houver áudio) - o orchestrator usa
            # esse caminho pra adicionar a transcrição como fonte extra no NotebookLM.
            transcript_path: Optional[Path] = None
            lesson_dir = (
                audio_paths[0].parent if audio_paths
                else slide_paths[0].parent if slide_paths
                else None
            )
            if lesson_dir and lesson_dir.exists() and "transcript" in result_json:
                candidate_path = lesson_dir / GENERATED_TRANSCRIPT_FILENAME
                candidate_path.write_text(result_json["transcript"], encoding="utf-8")
                logger.info(f"Transcrição salva com sucesso em: {candidate_path}")
                transcript_path = candidate_path

            # "tema" vem vazio ("") quando o Gemini não achou um título claro no
            # slide - trata como None pra quem chama não confundir com um tema real.
            tema = result_json.get("tema") or None
            flashcards = result_json.get("flashcards") or []

            # Rede de segurança: completa até o mínimo se o Gemini devolveu menos
            # (reaproveitando a mesma transcrição, sem reprocessar áudio/slide).
            flashcards = self._top_up_flashcards(flashcards, result_json.get("transcript"), unit_code, lesson_name)

            # Busca vídeos REAIS do YouTube (grounding via Google Search) pros tópicos
            # dos flashcards, um por tópico único - nunca aceita um ID que o Gemini só
            # "lembrou" de memória, só o que veio de um resultado de busca de verdade.
            self._enrich_flashcards_with_videos(flashcards)

            # Anexa as imagens de slide indicadas pelo Gemini (pergunta e/ou gabarito) -
            # ver _attach_slide_images pra detalhe de cada campo.
            if settings.flashcards.extract_slide_images:
                self._attach_slide_images(flashcards, [sp for sp in slide_paths if sp and sp.exists()], lesson_name)

            # Salva os flashcards gerados como JSON na pasta da aula (mesmo lugar da
            # transcrição) - é o que permite "gerar mais flashcards" depois (Streamlit)
            # saber quais já existem, pra pedir cards novos ao Gemini sem repetir.
            if lesson_dir and lesson_dir.exists() and flashcards:
                try:
                    (lesson_dir / "flashcards.json").write_text(
                        json.dumps(flashcards, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
                except Exception as e:
                    logger.warning(f"Não consegui salvar flashcards.json em {lesson_dir}: {e}")

            return {
                "success": True,
                "tema": tema,
                "transcript": result_json.get("transcript"),
                "summary": result_json.get("summary"),
                "transcript_path": transcript_path,
                "flashcards": flashcards,
                "error": None,
            }

        except Exception as e:
            logger.error(f"Erro crítico ao processar materiais no Gemini: {e}")
            return {
                "success": False,
                "tema": None,
                "transcript": None,
                "summary": None,
                "transcript_path": None,
                "flashcards": [],
                "error": str(e),
            }
        finally:
            for f_ref in uploaded_files:
                try:
                    self.client.files.delete(name=f_ref.name)
                except Exception:
                    pass
            for tmp_path in compressed_audio_paths:
                try:
                    tmp_path.unlink(missing_ok=True)
                except Exception:
                    pass


multimodal_processor = MultimodalProcessor()
