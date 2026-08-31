import os
import json
import json_repair
import shutil
import subprocess
from pathlib import Path
from typing import List, Dict, Any, Optional

from config.settings import settings
from core.slide_extractor import slide_extractor
from utils.logger import logger
from dotenv import load_dotenv

load_dotenv()

# Modelos padrão para geração de flashcards
DEFAULT_CLAUDE_MODEL = os.getenv("CLAUDE_FLASHCARD_MODEL", "auto/claude-sonnet")
FALLBACK_CLAUDE_MODELS = [
    "claude-3-7-sonnet-20250219",
    "claude-3-5-sonnet-20241022",
    "claude-3-5-haiku-20241022",
    "claude-sonnet-5",
]
MIN_FLASHCARDS_PER_LESSON = 15


class ClaudeClient:
    """Cliente para geração de flashcards médicos de alto rendimento utilizando Claude (Anthropic)."""

    def __init__(self):
        self.api_key = os.getenv("ANTHROPIC_API_KEY")
        self.base_url = os.getenv("ANTHROPIC_BASE_URL")
        self.model_name = DEFAULT_CLAUDE_MODEL
        self._anthropic_sdk_available = False

        try:
            import anthropic
            self._anthropic_sdk_available = True
        except ImportError:
            logger.debug("SDK 'anthropic' não instalado no ambiente Python.")

    def is_available(self) -> bool:
        """Verifica se o Claude está disponível via SDK (API Key) ou via CLI (claude -p)."""
        if self.api_key and self._anthropic_sdk_available:
            return True
        if shutil.which("claude"):
            return True
        return False

    def _get_anthropic_client(self):
        """Inicializa e retorna o cliente oficial do SDK Anthropic."""
        if not self._anthropic_sdk_available or not self.api_key:
            return None
        import anthropic
        kwargs: Dict[str, Any] = {"api_key": self.api_key}
        if self.base_url:
            kwargs["base_url"] = self.base_url
        return anthropic.Anthropic(**kwargs)

    @staticmethod
    def _parse_json_response(text: str, context: str = "claude") -> Any:
        """Processa a resposta JSON com json_repair como fallback em caso de erros de escape."""
        clean_text = text.strip()
        # Remove possíveis marcadores markdown de bloco ```json ... ```
        if clean_text.startswith("```"):
            lines = clean_text.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            clean_text = "\n".join(lines).strip()

        try:
            return json.loads(clean_text, strict=False)
        except json.JSONDecodeError as e:
            logger.warning(
                f"JSON malformado na resposta do Claude ({context}): {e}. "
                f"Tentando recuperar com json_repair..."
            )
            try:
                repaired = json_repair.loads(clean_text)
                if repaired:
                    logger.info(f"JSON reparado com sucesso via json_repair ({context}).")
                    return repaired
            except Exception as repair_err:
                logger.error(f"json_repair falhou ({context}): {repair_err}")
            raise e

    def _build_prompt(
        self,
        lesson_name: str,
        unit_code: str,
        transcript: str,
        slide_paths: List[Path],
        min_cards: int = MIN_FLASHCARDS_PER_LESSON,
        existing_cards: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        """Constrói o prompt especializado para geração médica com transcrição e slides."""
        # 1. Estrutura o conteúdo dos slides página a página
        slides_content_blocks = []
        for sp in slide_paths:
            if not sp.exists():
                continue
            pages_data = slide_extractor.extract_slide_text_by_page(sp)
            slide_pages_text = []
            for p in pages_data:
                img_tag = " [TEM IMAGENS/DIAGRAMAS/ESQUEMAS]" if p.get("has_images") else ""
                slide_pages_text.append(
                    f"--- Slide {p['page_number']}{img_tag} ---\n{p['text'] or '(Conteúdo puramente visual/sem texto puro)'}"
                )
            slides_content_blocks.append(
                f"Arquivo de Slide: '{sp.name}' ({len(pages_data)} páginas)\n" + "\n".join(slide_pages_text)
            )

        slides_section = (
            "\n\n==================== ESTRUTURA DOS SLIDES ====================\n"
            + "\n\n".join(slides_content_blocks)
            if slides_content_blocks
            else "(Nenhum arquivo de slide fornecido)"
        )

        slide_names_list = ", ".join(sp.name for sp in slide_paths if sp.exists()) or "(nenhum)"

        # 2. Contexto de cards já existentes (caso seja incremento)
        existing_block = ""
        if existing_cards:
            existing_questions = [
                (c.get("enunciado") or c.get("assertiva") or "").strip()
                for c in existing_cards
                if (c.get("enunciado") or c.get("assertiva"))
            ]
            if existing_questions:
                existing_block = (
                    "\nOs seguintes flashcards JÁ EXISTEM para esta aula. NÃO repita as mesmas perguntas:\n"
                    + "\n".join(f"- {q}" for q in existing_questions)
                    + "\n"
                )

        prompt = f"""
Você é um médico especialista, preceptor sênior e especialista em metodologias ativas de aprendizado médico (padrão USMLE / Revalida / Residência Médica).

Analise profundamente os materiais da aula '{lesson_name}' da unidade curricular '{unit_code}'.
Você recebeu:
1. A TRANSCRIÇÃO LITERAL COMPLETA de tudo o que foi falado em sala de aula.
2. A ESTRUTURA E CONTEÚDO PÁGINA A PÁGINA de todos os slides apresentados.
Arquivos de slides: {slide_names_list}
{existing_block}
Sua missão é gerar PELO MENOS {min_cards} flashcards de altíssimo rendimento (high-yield) para o Anki.
Cubra os tópicos essenciais: fisiopatologia, critérios diagnósticos, farmacologia/doses/efeitos, raciocínio clínico diferencial, condutas e pegadinhas clássicas.

Gere cartões dos dois tipos: "mc" (múltipla escolha) e "vf" (verdadeiro ou falso).

FORMATO EXATO DE CADA ITEM NO JSON:

Para itens do tipo "mc" (Múltipla Escolha):
- "tipo": "mc"
- "topico_busca": Termo conciso (3 a 8 palavras) para busca de vídeo educacional no YouTube (ex.: "Pneumonia adquirida na comunidade tratamento").
- "enunciado": A pergunta ou vinheta clínica detalhada.
- "resposta_correta": A alternativa correta.
- "opcoes_erradas": Lista contendo de 2 a 7 alternativas incorretas, porém plausíveis e clinicamente desafiadoras (distratores de qualidade).
- "pegadinha": Se houver distrator tentador, explique o porquê do erro em 1-2 frases; caso contrário, use "" (string vazia).
- "explicacao": Inicie OBRIGATORIAMENTE com "💡 GABARITO COMENTADO: [TÓPICO]" (substitua [TÓPICO] pelo assunto específico) e fundamente detalhadamente o raciocínio fisiopatológico e clínico da resposta correta.
- "fonte": Nome do slide e página exata (ex.: "{slide_names_list.split(',')[0]}, slide 4").
- "imagem_slide_pagina": Número inteiro da página do slide (1-based) para ser exibida NA FRENTE DO CARD (lado da pergunta/antes de responder).
  * REGRA FUNDAMENTAL E OBRIGATÓRIA: Utilize SOMENTE quando o slide tiver uma imagem clínica/exame/esquema anatômico e NÃO CONTIVER texto, legenda ou rótulo que entregue a resposta da pergunta. Se a imagem contiver o nome da estrutura ou a resposta explícita, deixe como null. Na dúvida, use null.
- "imagem_slide_pagina_gabarito": Número inteiro da página do slide (1-based) para ser exibida NO VERSO DO CARD (lado da explicação/após responder).
  * Aqui é o local ideal para fluxogramas de conduta, tabelas com doses, esquemas rotulados e resumos visuais que confirmem e enriqueçam a explicação. Use sempre que houver um slide visualmente enriquecedor para aquele tópico (inteiro ou null).

Para itens do tipo "vf" (Verdadeiro ou Falso):
- "tipo": "vf"
- "topico_busca": Termo para busca de vídeo (igual acima).
- "contexto_enunciado": Breve contextualização clínica (opcional, pode ser "").
- "assertiva": A afirmação direta a ser julgada pelo estudante.
- "gabarito": OBRIGATORIAMENTE "Verdadeiro" ou "Falso".
- "pegadinha": Explicação de pegadinha ou "" se não houver.
- "explicacao": Comece com "💡 GABARITO COMENTADO: [TÓPICO]" seguido da justificativa completa.
- "fonte": Nome do slide e número da página.
- "imagem_slide_pagina": Igual às regras acima (inteiro ou null).
- "imagem_slide_pagina_gabarito": Igual às regras acima (inteiro ou null).

Retorne ESTRITAMENTE um objeto JSON válido no formato:
{{
  "flashcards": [
    ...
  ]
}}

{slides_section}

==================== TRANSCRIÇÃO DA AULA ====================
{transcript}
==============================================================
"""
        return prompt.strip()

    def generate_flashcards(
        self,
        lesson_name: str,
        unit_code: str,
        transcript: str,
        slide_paths: List[Path],
        min_cards: int = MIN_FLASHCARDS_PER_LESSON,
        existing_cards: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Gera flashcards utilizando Claude (via SDK Anthropic ou CLI claude -p).
        Retorna {"success": bool, "flashcards": list, "error": str | None, "model_used": str}."""
        if not transcript and not slide_paths:
            return {
                "success": False,
                "flashcards": [],
                "error": "Nenhum material (transcrição ou slide) fornecido para geração de flashcards.",
                "model_used": None,
            }

        prompt = self._build_prompt(
            lesson_name=lesson_name,
            unit_code=unit_code,
            transcript=transcript,
            slide_paths=slide_paths,
            min_cards=min_cards,
            existing_cards=existing_cards,
        )

        # 1. Tentativa via SDK Anthropic
        client = self._get_anthropic_client()
        if client:
            models_to_try = [self.model_name] + [m for m in FALLBACK_CLAUDE_MODELS if m != self.model_name]
            last_err = None
            for model in models_to_try:
                try:
                    logger.info(f"Gerando flashcards via Claude SDK (modelo: {model})...")
                    response = client.messages.create(
                        model=model,
                        max_tokens=8192,
                        messages=[{"role": "user", "content": prompt}],
                    )
                    text_response = "".join(
                        getattr(block, "text", "")
                        for block in response.content
                        if getattr(block, "type", "") == "text" or hasattr(block, "text")
                    )
                    data = self._parse_json_response(text_response, f"claude_sdk_{model}")
                    flashcards = data.get("flashcards") or []
                    if flashcards:
                        logger.info(f"Claude gerou com sucesso {len(flashcards)} flashcards de alto rendimento.")
                        self._attach_slide_images(flashcards, slide_paths, lesson_name)
                        return {
                            "success": True,
                            "flashcards": flashcards,
                            "error": None,
                            "model_used": f"claude_sdk:{model}",
                        }
                except Exception as e:
                    last_err = e
                    logger.warning(f"Tentativa com modelo Claude '{model}' falhou: {e}")

            logger.warning(f"Todas as tentativas via Claude SDK falharam: {last_err}")

        # 2. Tentativa via CLI (claude -p)
        claude_bin = shutil.which("claude")
        if claude_bin:
            try:
                logger.info("Tentando geração de flashcards via Claude Code CLI (claude -p)...")
                process = subprocess.run(
                    [
                        claude_bin,
                        "-p",
                        prompt,
                        "--output-format",
                        "text",
                        "--tools",
                        "",
                    ],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=300,
                )
                if process.returncode == 0 and process.stdout.strip():
                    data = self._parse_json_response(process.stdout, "claude_cli")
                    flashcards = data.get("flashcards") or []
                    if flashcards:
                        logger.info(f"Claude CLI gerou com sucesso {len(flashcards)} flashcards.")
                        self._attach_slide_images(flashcards, slide_paths, lesson_name)
                        return {
                            "success": True,
                            "flashcards": flashcards,
                            "error": None,
                            "model_used": "claude_cli",
                        }
                else:
                    logger.warning(f"Claude CLI retornou código {process.returncode}: {process.stderr[:300]}")
            except Exception as e:
                logger.warning(f"Execução do Claude CLI falhou: {e}")

        return {
            "success": False,
            "flashcards": [],
            "error": "Claude indisponível ou falhou em todas as tentativas.",
            "model_used": None,
        }

    def _attach_slide_images(
        self, flashcards: List[Dict[str, Any]], slide_paths: List[Path], lesson_name: str
    ) -> None:
        """Renderiza as páginas de slides em PNG indicadas pelo Claude e anexa aos cards."""
        if not slide_paths:
            return

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

                # Localiza o slide citado ou usa o primeiro disponível
                slide = self._pick_slide_for_card(card, slide_paths)
                if not slide:
                    continue
                try:
                    image_path = slide_extractor.extract_single_page(slide, page, lesson_name)
                    card[target_field] = str(image_path)
                except Exception as e:
                    logger.warning(
                        f"Não foi possível extrair página {page} de '{slide.name}' pro card ({page_field}): {e}"
                    )

    @staticmethod
    def _pick_slide_for_card(card: Dict[str, Any], slide_paths: List[Path]) -> Optional[Path]:
        """Localiza o slide correspondente citado no campo fonte."""
        if not slide_paths:
            return None
        if len(slide_paths) == 1:
            return slide_paths[0]
        fonte = (card.get("fonte") or "").lower()
        for sp in slide_paths:
            if sp.name.lower() in fonte:
                return sp
        return slide_paths[0]


claude_client = ClaudeClient()
