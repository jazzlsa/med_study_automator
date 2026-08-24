import hashlib
from pathlib import Path
from typing import List, Optional
import genanki
import requests
from config.settings import settings
from core.schemas import FlashcardItem
from utils.logger import logger

# CSS Médico Unificado com suporte a NightMode, Badges e Imagens
CSS_MEDICINA_PREMIUM = """
.card {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    font-size: 19px;
    text-align: left;
    color: #1e293b;
    background-color: #ffffff;
    padding: 24px;
    border-radius: 12px;
    line-height: 1.6;
    max-width: 750px;
    margin: 0 auto;
}

.nightMode .card {
    color: #f1f5f9;
    background-color: #0f172a;
}

.cloze {
    font-weight: 700;
    color: #2563eb;
    background-color: #eff6ff;
    padding: 2px 6px;
    border-radius: 4px;
    border-bottom: 2px solid #3b82f6;
}

.nightMode .cloze {
    color: #60a5fa;
    background-color: #1e293b;
    border-bottom: 2px solid #60a5fa;
}

.tag-badge {
    display: inline-block;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    font-weight: 700;
    color: #047857;
    background-color: #d1fae5;
    padding: 3px 8px;
    border-radius: 9999px;
    margin-right: 6px;
    margin-bottom: 14px;
}

.nightMode .tag-badge {
    color: #34d399;
    background-color: #064e3b;
}

.extra-box {
    margin-top: 18px;
    padding: 14px 18px;
    background-color: #f8fafc;
    border-left: 4px solid #3b82f6;
    border-radius: 0 8px 8px 0;
    font-size: 17px;
}

.nightMode .extra-box {
    background-color: #1e293b;
    border-left: 4px solid #60a5fa;
}

.slide-box {
    margin-top: 16px;
    padding: 10px;
    background: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 8px;
    text-align: center;
}

.nightMode .slide-box {
    background: #1e293b;
    border: 1px solid #334155;
}

.slide-box img {
    max-width: 100%;
    height: auto;
    border-radius: 6px;
    box-shadow: 0 4px 6px -1px rgb(0 0 0 / 0.1);
}

hr#answer {
    border: 0;
    height: 1px;
    background: #cbd5e1;
    margin: 18px 0;
}

.nightMode hr#answer {
    background: #334155;
}

/* Suporte para Image Occlusion Nativo do Anki */
#image-occlusion-canvas {
    --inactive-shape-color: #ffeba2;
    --active-shape-color: #ff8e8e;
    --inactive-shape-border: 1px #212121;
    --active-shape-border: 1px #212121;
    --highlight-shape-color: #ff8e8e00;
    --highlight-shape-border: 1px #ff8e8e;
}
"""

MED_BASIC_MODEL_ID = 1787540630026
MED_CLOZE_MODEL_ID = 1787540630030

# Modelo Basic Clínico
med_basic_model = genanki.Model(
    MED_BASIC_MODEL_ID,
    "MedStudy Standard Basic",
    fields=[
        {"name": "Front"},
        {"name": "Back"},
        {"name": "Back Extra"},
        {"name": "Tags"},
        {"name": "SlideMedia"},
    ],
    templates=[
        {
            "name": "Card 1",
            "qfmt": '{{#Tags}}<span class="tag-badge">{{Tags}}</span><br>{{/Tags}}<div class="question-text">{{Front}}</div>',
            "afmt": '{{FrontSide}}<hr id="answer"><div class="answer-text">{{Back}}</div>{{#Back Extra}}<div class="extra-box">{{Back Extra}}</div>{{/Back Extra}}{{#SlideMedia}}<div class="slide-box">{{SlideMedia}}</div>{{/SlideMedia}}',
        }
    ],
    css=CSS_MEDICINA_PREMIUM,
)

# Modelo Cloze com Extra Clínico
med_cloze_model = genanki.Model(
    MED_CLOZE_MODEL_ID,
    "MedStudy Standard Cloze",
    fields=[
        {"name": "Text"},
        {"name": "Back Extra"},
        {"name": "Tags"},
        {"name": "SlideMedia"},
    ],
    templates=[
        {
            "name": "Cloze Card",
            "qfmt": '{{#Tags}}<span class="tag-badge">{{Tags}}</span><br>{{/Tags}}{{cloze:Text}}',
            "afmt": '{{#Tags}}<span class="tag-badge">{{Tags}}</span><br>{{/Tags}}{{cloze:Text}}<hr id="answer">{{#Back Extra}}<div class="extra-box">{{Back Extra}}</div>{{/Back Extra}}{{#SlideMedia}}<div class="slide-box">{{SlideMedia}}</div>{{/SlideMedia}}',
        }
    ],
    css=CSS_MEDICINA_PREMIUM,
    model_type=genanki.Model.CLOZE,
)


def _generate_deterministic_id(seed_text: str) -> int:
    """Gera um ID numérico determinístico baseado no nome do baralho."""
    return int(hashlib.md5(seed_text.encode("utf-8")).hexdigest()[:8], 16)


class AnkiDeckCompiler:
    """Compila flashcards estruturados em arquivos .apkg e integra com AnkiConnect."""

    def __init__(self, output_dir: Optional[Path] = None):
        if output_dir:
            self.output_dir = output_dir
        elif hasattr(settings, "storage") and hasattr(settings.storage, "apkg_output_dir"):
            self.output_dir = settings.storage.apkg_output_dir
        else:
            self.output_dir = Path("outputs")
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def compile_apkg(
        self,
        deck_name: str,
        lesson_name: str,
        flashcards: List[FlashcardItem],
        media_files: Optional[List[Path]] = None,
    ) -> Path:
        """Gera pacote .apkg com mídia embutida e layout médico padronizado."""
        deck_id = _generate_deterministic_id(f"{deck_name}::{lesson_name}")
        full_deck_title = f"{deck_name}::{lesson_name}"
        deck = genanki.Deck(deck_id, full_deck_title)
        package = genanki.Package(deck)

        media_map = {}
        if media_files:
            for m in media_files:
                p = Path(m)
                if p.exists():
                    package.media_files.append(str(p))
                    media_map[p.name] = f'<img src="{p.name}">'

        for card in flashcards:
            tag_str = " ".join([t.replace(" ", "_") for t in card.tags])
            slide_media_html = ""

            if card.slide_page_reference:
                ref_filename = f"slide_page_{card.slide_page_reference:03d}.png"
                if ref_filename in media_map:
                    slide_media_html = media_map[ref_filename]

            if card.card_type == "Cloze":
                note = genanki.Note(
                    model=med_cloze_model,
                    fields=[card.front, card.back, tag_str, slide_media_html],
                    tags=card.tags,
                )
            else:
                note = genanki.Note(
                    model=med_basic_model,
                    fields=[card.front, card.back, "", tag_str, slide_media_html],
                    tags=card.tags,
                )
            deck.add_note(note)

        sanitized_lesson = lesson_name.replace(" ", "_")
        apkg_filename = f"{deck_name.replace('::', '_')}_{sanitized_lesson}.apkg"
        output_file = self.output_dir / apkg_filename
        package.write_to_file(output_file)

        logger.info(f"📦 Pacote Anki (.apkg) gerado com sucesso: {output_file.name}")
        return output_file

    def sync_with_ankiconnect(
        self, apkg_path: Path, anki_url: Optional[str] = None
    ) -> bool:
        """Envia o arquivo .apkg diretamente para o Anki aberto via AnkiConnect."""
        if anki_url:
            url = anki_url
        elif hasattr(settings, "anki") and hasattr(settings.anki, "ankiconnect_url"):
            url = settings.anki.ankiconnect_url
        else:
            url = "http://localhost:8765"

        payload = {
            "action": "importPackage",
            "version": 6,
            "params": {"path": str(apkg_path.resolve())},
        }

        try:
            response = requests.post(url, json=payload, timeout=5)
            res_data = response.json()
            if res_data.get("error") is None:
                logger.success("⚡ Baralho importado com sucesso para o Anki local via AnkiConnect!")
                return True
            else:
                logger.warning(f"AnkiConnect retornou erro: {res_data.get('error')}")
                return False
        except requests.exceptions.RequestException:
            logger.warning("⚠️ Anki desktop não detectado ou AnkiConnect inativo. O arquivo .apkg está pronto para abertura manual.")
            return False


anki_compiler = AnkiDeckCompiler()