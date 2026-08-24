from typing import List, Literal, Optional
from pydantic import BaseModel, Field


class FlashcardItem(BaseModel):
    """Modelo estruturado para cada Flashcard gerado."""

    card_type: Literal["Basic", "Cloze"] = Field(
        default="Basic",
        description="Tipo do card: 'Basic' para Pergunta/Resposta ou 'Cloze' para Omissão de Palavras.",
    )
    front: str = Field(
        description="Frente do flashcard (pergunta, caso clínico conciso ou texto com oclusão {{c1::...}})."
    )
    back: str = Field(
        description="Verso do flashcard (resposta objetiva, mecanismo fisiopatológico ou conduta)."
    )
    slide_page_reference: Optional[int] = Field(
        default=None,
        description="Número da página do slide correspondente ao conceito do card (se houver).",
    )
    tags: List[str] = Field(
        default_factory=list,
        description="Tags médicas (ex: 'fisiopatologia', 'diagnostico', 'farmacologia').",
    )


class StudySummary(BaseModel):
    """Resumo conceitual da aula para revisão rápida."""

    title: str = Field(description="Título formal do tema da aula.")
    core_concepts: List[str] = Field(description="Lista dos principais conceitos fisiopatológicos e clínicos.")
    clinical_pearls: List[str] = Field(
        description="Dicas clínicas de alta relevância prática e pontos de prova."
    )


class LessonProcessingResult(BaseModel):
    """Payload completo retornado pelo pipeline do Gemini."""

    summary: StudySummary
    flashcards: List[FlashcardItem]