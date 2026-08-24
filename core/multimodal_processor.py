import os
import json
from pathlib import Path
from typing import List, Dict, Any, Optional
from google import genai
from google.genai import types
from utils.logger import logger
from dotenv import load_dotenv

load_dotenv()

class MultimodalProcessor:
    """Processa múltiplos arquivos (PDFs e áudios) usando a API do Gemini 2.5/3.6 Flash."""

    def __init__(self):
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            logger.warning("GEMINI_API_KEY não encontrada no ambiente.")
        self.client = genai.Client(api_key=api_key)
        self.model_name = "gemini-2.5-flash"  # Ou gemini-3.6-flash conforme configurado

    def analyze_lesson_materials(
        self, 
        slide_paths: List[Path], 
        audio_paths: List[Path], 
        lesson_name: str, 
        unit_code: str
    ) -> Dict[str, Any]:
        """Envia múltiplos slides e áudios para o Gemini extrair resumos estruturados e flashcards no formato correto."""
        
        uploaded_files = []
        try:
            prompt = f"""
            Você é um especialista médico sênior e professor de medicina. 
            Analise os materiais fornecidos para a aula '{lesson_name}' da unidade '{unit_code}'.
            
            Sua tarefa é gerar um JSON estrito contendo:
            1. "summary": Um resumo clínico estruturado, focado em fisiopatologia, diagnóstico, conduta e pérolas clínicas.
            2. "flashcards": Uma lista de flashcards rigorosamente dividida entre cartões do tipo "basic" (frente e verso) e "cloze" (com lacunas no formato Anki {{c1::termo}}).
            
            O JSON de saída deve ter exatamente esta estrutura:
            {{
              "summary": "Texto do resumo em markdown...",
              "flashcards": [
                {{
                  "type": "basic",
                  "header": "{unit_code} - {lesson_name}",
                  "front": "Qual a principal indicação de...",
                  "back": "A indicação principal é...",
                  "clinical_pearl": "Sempre atentar para o sinal clínico X."
                }},
                {{
                  "type": "cloze",
                  "header": "{unit_code} - {lesson_name}",
                  "text": "O tratamento de primeira linha para a condição é a {{c1::hidratação venosa}} associada a {{c2::antibioticoterapia}}.",
                  "back_extra": "Evitar corticoide precoce em pacientes com infecção fúngica."
                }}
              ]
            }}
            Retorne APENAS o JSON puro, sem blocos de código markdown adicionais se possível, ou garantindo que seja um JSON válido.
            """

            contents = [prompt]

            # Envia múltiplos arquivos de slides (PDF)
            for sp in slide_paths:
                if sp and sp.exists():
                    f_ref = self.client.files.upload(file=str(sp))
                    uploaded_files.append(f_ref)
                    contents.append(f_ref)

            # Envia múltiplos arquivos de áudio (MP3/M4A)
            for ap in audio_paths:
                if ap and ap.exists():
                    f_ref = self.client.files.upload(file=str(ap))
                    uploaded_files.append(f_ref)
                    contents.append(f_ref)

            response = self.client.models.generate_content(
                model=self.model_name,
                contents=contents,
                config=types.GenerateContentConfig(
                    temperature=0.2,
                    response_mime_type="application/json"
                )
            )

            result_json = json.loads(response.text)
            return result_json

        except Exception as e:
            logger.error(f"Erro ao processar materiais no Gemini: {e}")
            return {
                "summary": f"Erro ao gerar resumo automático: {e}",
                "flashcards": []
            }
        finally:
            # Limpa arquivos enviados para o storage temporário do Gemini
            for f_ref in uploaded_files:
                try:
                    self.client.files.delete(name=f_ref.name)
                except Exception:
                    pass

multimodal_processor = MultimodalProcessor()