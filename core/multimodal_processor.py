import os
import json
import time
from pathlib import Path
from typing import List, Dict, Any, Optional
from google import genai
from google.genai import types
from utils.logger import logger
from dotenv import load_dotenv

load_dotenv()

class MultimodalProcessor:
    """Processa arquivos multimodais com tentativas automáticas em caso de picos de demanda."""

    def __init__(self):
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            logger.error("ATENÇÃO: GEMINI_API_KEY não foi encontrada no arquivo .env ou no ambiente!")
        
        self.client = genai.Client(api_key=api_key) if api_key else genai.Client()
        self.model_name = "gemini-3.6-flash"

    def analyze_lesson_materials(
        self, 
        slide_paths: List[Path], 
        audio_paths: List[Path], 
        lesson_name: str, 
        unit_code: str
    ) -> Dict[str, Any]:
        """Envia slides e áudios para o Gemini com política de re-tentativa (Retry) para evitar erros 503."""
        uploaded_files = []
        try:
            prompt = f"""
            Você é um médico especialista e professor sênior. 
            Analise rigorosamente os materiais fornecidos (slides e áudio) para a aula '{lesson_name}' da unidade '{unit_code}'.
            
            Sua tarefa é retornar estritamente um JSON contendo:
            1. "transcript": Uma transcrição detalhada, fluida e estruturada de todo o conteúdo falado no áudio em formato de texto corrido acadêmico.
            2. "summary": Um resumo clínico estruturado, focado em fisiopatologia, diagnóstico, conduta e pérolas clínicas.
            
            Retorne EXATAMENTE um JSON válido no seguinte formato e nada mais:
            {{
              "transcript": "Transcrição detalhada do áudio...",
              "summary": "Resumo clínico detalhado..."
            }}
            """

            contents = [prompt]
            for sp in slide_paths:
                if sp and sp.exists():
                    logger.info(f"Fazendo upload do slide para o Gemini: {sp.name}")
                    f_ref = self.client.files.upload(file=str(sp))
                    uploaded_files.append(f_ref)
                    contents.append(f_ref)

            for ap in audio_paths:
                if ap and ap.exists():
                    logger.info(f"Fazendo upload do áudio para o Gemini: {ap.name}")
                    f_ref = self.client.files.upload(file=str(ap))
                    uploaded_files.append(f_ref)
                    contents.append(f_ref)

            # Sistema de Tentativas (Retry) para contornar picos de trânsito (503)
            max_retries = 3
            response = None
            
            for attempt in range(max_retries):
                try:
                    logger.info(f"Enviando requisição multimodal (Tentativa {attempt + 1}/{max_retries})...")
                    response = self.client.models.generate_content(
                        model=self.model_name,
                        contents=contents,
                        config=types.GenerateContentConfig(
                            temperature=0.2,
                            response_mime_type="application/json"
                        )
                    )
                    break
                except Exception as api_err:
                    if attempt < max_retries - 1:
                        logger.warning(f"Servidor com alta demanda (Tentativa {attempt + 1}), aguardando 10s para tentar novamente...")
                        time.sleep(10)
                    else:
                        raise api_err

            logger.info("Resposta bruta do Gemini recebida com sucesso.")
            result_json = json.loads(response.text)

            # Salva automaticamente a transcrição como arquivo .txt na pasta da aula
            if audio_paths and audio_paths[0].parent.exists() and "transcript" in result_json:
                transcript_path = audio_paths[0].parent / "transcricao_aula.txt"
                transcript_path.write_text(result_json["transcript"], encoding="utf-8")
                logger.info(f"Transcrição salva com sucesso em: {transcript_path}")

            return result_json

        except Exception as e:
            logger.error(f"Erro crítico ao processar materiais no Gemini: {e}")
            return {
                "transcript": f"Erro ao gerar transcrição: {e}",
                "summary": f"Erro ao gerar resumo automático: {e}"
            }
        finally:
            for f_ref in uploaded_files:
                try:
                    self.client.files.delete(name=f_ref.name)
                except Exception:
                    pass

multimodal_processor = MultimodalProcessor()