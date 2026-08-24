import json
import time
from pathlib import Path
from typing import Optional, Tuple
from google import genai
from google.genai import types
from google.genai.errors import ServerError, ClientError

from config.settings import settings
from core.schemas import LessonProcessingResult
from utils.logger import logger

SYSTEM_PROMPT_MEDICINA = """
Você é um preceptor médico sênior e especialista em educação médica baseada em evidências.
Sua missão é analisar os materiais de aula fornecidos (slides em PDF e/ou gravação em áudio) e gerar:

1. Resumo conceitual estruturado com fisiopatologia, critérios diagnósticos, farmacologia e conduta terapêutica.
2. Conjunto de flashcards de alta qualidade no formato Anki (Basic e Cloze), focados em raciocínio clínico e memorização espaçada de alto rendimento.

Diretrizes para os Flashcards:
- Evite perguntas genéricas ou triviais. Foque no mecanismo de ação, apresentações atípicas, diagnóstico diferencial e condutas.
- Para cards do tipo 'Basic', faça perguntas clínicas pontuais ou pequenos vinhetas de casos.
- Para cards do tipo 'Cloze', utilize a sintaxe de oclusão do Anki: {{c1::termo_chave}}.
- Associe o 'slide_page_reference' sempre que o conceito for derivado diretamente de um slide visual ou tabela.
- Use tags padronizadas em minúsculas (ex: endocrinologia, fisiopatologia, farmacologia, emergencia).
"""


class GeminiMedicalClient:
    """Cliente de integração com a API do Gemini Flash para processamento multimodal."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or settings.secrets.GEMINI_API_KEY
        if not self.api_key or self.api_key == "sua_chave_gemini_aqui":
            logger.warning("⚠️ GEMINI_API_KEY não configurada no arquivo .env!")
        self.client = genai.Client(api_key=self.api_key)

    def upload_file(self, file_path: Path) -> types.File:
        """Faz upload de arquivo para a File API do Gemini e aguarda processamento."""
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"Arquivo não encontrado para upload: {path}")

        logger.info(f"Enviando arquivo para API do Gemini: {path.name}")
        uploaded = self.client.files.upload(file=path)

        while uploaded.state.name == "PROCESSING":
            logger.debug("Aguardando processamento do arquivo no Gemini...")
            time.sleep(2)
            uploaded = self.client.files.get(name=uploaded.name)

        if uploaded.state.name == "FAILED":
            raise RuntimeError(f"Falha no processamento do arquivo {path.name} no Gemini.")

        logger.info(f"Arquivo pronto: {uploaded.display_name} (URI: {uploaded.uri})")
        return uploaded

    def process_lesson_materials(
        self,
        unit_name: str,
        lesson_title: str,
        slide_path: Optional[Path] = None,
        audio_path: Optional[Path] = None,
        max_retries: int = 3,
    ) -> Tuple[LessonProcessingResult, int, int]:
        """Envia slides e áudio para o Gemini com retry automático em caso de sobrecarga."""
        uploaded_files = []
        contents = []

        if slide_path and Path(slide_path).exists():
            slide_file = self.upload_file(Path(slide_path))
            uploaded_files.append(slide_file)
            contents.append(slide_file)

        if audio_path and Path(audio_path).exists():
            audio_file = self.upload_file(Path(audio_path))
            uploaded_files.append(audio_file)
            contents.append(audio_file)

        user_prompt = f"""
        Unidade Curricular: {unit_name}
        Tema da Aula: {lesson_title}

        Analise cuidadosamente o material em anexo e gere o resumo e os flashcards estruturados conforme o schema exigido.
        """
        contents.append(user_prompt)

        logger.info(f"Gerando flashcards e resumo para '{lesson_title}' com Gemini...")

        response = None
        for attempt in range(1, max_retries + 1):
            try:
                response = self.client.models.generate_content(
                    model="gemini-3.6-flash",
                    contents=contents,
                    config=types.GenerateContentConfig(
                        system_instruction=SYSTEM_PROMPT_MEDICINA,
                        response_mime_type="application/json",
                        response_schema=LessonProcessingResult,
                        temperature=0.2,
                    ),
                )
                break
            except (ServerError, ClientError) as err:
                if attempt == max_retries:
                    logger.error(f"Tentativa {attempt}/{max_retries} falhou definitivamente: {err}")
                    raise err
                wait_sec = attempt * 4
                logger.warning(
                    f"Servidor ocupado (tentativa {attempt}/{max_retries}). Aguardando {wait_sec}s para tentar novamente..."
                )
                time.sleep(wait_sec)

        for f in uploaded_files:
            try:
                self.client.files.delete(name=f.name)
            except Exception as e:
                logger.debug(f"Não foi possível deletar arquivo remoto {f.name}: {e}")

        prompt_tokens = response.usage_metadata.prompt_token_count if response.usage_metadata else 0
        completion_tokens = (
            response.usage_metadata.candidates_token_count if response.usage_metadata else 0
        )

        result_data = json.loads(response.text)
        result = LessonProcessingResult(**result_data)

        logger.success(
            f"Processamento concluído: {len(result.flashcards)} flashcards gerados | {len(result.summary.clinical_pearls)} clinical pearls."
        )

        return result, prompt_tokens, completion_tokens


gemini_client = GeminiMedicalClient()