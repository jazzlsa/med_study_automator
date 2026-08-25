import os
import json
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
from utils.logger import logger
from dotenv import load_dotenv

load_dotenv()

# Modelo principal e modelos de fallback (família Gemini 3.x), usados nessa ordem
# quando o principal esgota as tentativas por sobrecarga (503). Confirmados como
# disponíveis via client.models.list() em 2026-08-24.
PRIMARY_MODEL = "gemini-3.6-flash"
FALLBACK_MODELS = ["gemini-3.7-flash", "gemini-3.5-flash"]

MAX_RETRIES_PER_MODEL = 5
BASE_BACKOFF_SECONDS = 4
MAX_BACKOFF_SECONDS = 60

FILE_ACTIVE_POLL_INTERVAL_SECONDS = 2
FILE_ACTIVE_POLL_TIMEOUT_SECONDS = 120

# Bitrate/canal alvo ao recomprimir áudio antes do upload (reduz payload = menor
# chance de 503 em arquivos pesados). Só é usado se o ffmpeg estiver instalado.
AUDIO_COMPRESSION_BITRATE = "64k"
AUDIO_COMPRESSION_TIMEOUT_SECONDS = 180


class GeminiOverloadedError(Exception):
    """Levantado quando o modelo principal e todos os fallbacks esgotam as tentativas por 503."""


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

    def _upload_and_wait(self, path: Path, uploaded_files: list) -> Any:
        f_ref = self.client.files.upload(file=str(path))
        uploaded_files.append(f_ref)
        return self._wait_for_active(f_ref)

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
        de retry no mesmo modelo (a cota é por modelo, então insistir não ajuda)."""
        models_to_try = [self.model_name] + [m for m in FALLBACK_MODELS if m != self.model_name]
        last_error: Optional[Exception] = None

        for model in models_to_try:
            for attempt in range(1, MAX_RETRIES_PER_MODEL + 1):
                try:
                    logger.info(
                        f"Enviando requisição multimodal para o modelo {model} "
                        f"(tentativa {attempt}/{MAX_RETRIES_PER_MODEL})..."
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
            2. "transcript": Uma transcrição detalhada, fluida e estruturada de todo o conteúdo falado no áudio em formato de texto corrido acadêmico.
            3. "summary": Um resumo clínico estruturado, focado em fisiopatologia, diagnóstico, conduta e pérolas clínicas.
            4. "flashcards": uma lista de flashcards de alto rendimento cobrindo os pontos-chave
               da aula (fisiopatologia, critérios diagnósticos, farmacologia, conduta - evite
               perguntas triviais/genéricas). Quantidade proporcional ao conteúdo real da aula,
               sem forçar um número fixo. Cada item deve ter "tipo": "mc" (múltipla escolha) ou
               "tipo": "vf" (verdadeiro ou falso), com os campos:

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

               Para "tipo": "vf":
                 - "topico_busca": igual acima.
                 - "contexto_enunciado": frase curta de contexto (pode ser "").
                 - "assertiva": a afirmação a ser julgada.
                 - "gabarito": EXATAMENTE "Verdadeiro" ou "Falso".
                 - "pegadinha": igual acima.
                 - "explicacao": igual acima, começando com "💡 GABARITO COMENTADO: [TÓPICO]".
                 - "fonte": igual acima.

            Retorne EXATAMENTE um JSON válido no seguinte formato e nada mais:
            {{
              "tema": "Título exato copiado do slide...",
              "transcript": "Transcrição detalhada do áudio...",
              "summary": "Resumo clínico detalhado...",
              "flashcards": [
                {{"tipo": "mc", "topico_busca": "...", "enunciado": "...", "resposta_correta": "...", "opcoes_erradas": ["...", "..."], "pegadinha": "", "explicacao": "💡 GABARITO COMENTADO: ...", "fonte": "..."}},
                {{"tipo": "vf", "topico_busca": "...", "contexto_enunciado": "...", "assertiva": "...", "gabarito": "Verdadeiro", "pegadinha": "", "explicacao": "💡 GABARITO COMENTADO: ...", "fonte": "..."}}
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
            # strict=False: o Gemini às vezes retorna quebras de linha/controle não
            # escapadas dentro dos valores string (comum em transcrições longas);
            # o parser estrito do Python rejeitaria isso com "Invalid control character".
            result_json = json.loads(response.text, strict=False)

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
                candidate_path = lesson_dir / "transcricao_aula.txt"
                candidate_path.write_text(result_json["transcript"], encoding="utf-8")
                logger.info(f"Transcrição salva com sucesso em: {candidate_path}")
                transcript_path = candidate_path

            # "tema" vem vazio ("") quando o Gemini não achou um título claro no
            # slide - trata como None pra quem chama não confundir com um tema real.
            tema = result_json.get("tema") or None
            flashcards = result_json.get("flashcards") or []

            # Busca vídeos REAIS do YouTube (grounding via Google Search) pros tópicos
            # dos flashcards, um por tópico único - nunca aceita um ID que o Gemini só
            # "lembrou" de memória, só o que veio de um resultado de busca de verdade.
            self._enrich_flashcards_with_videos(flashcards)

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
