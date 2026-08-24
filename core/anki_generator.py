import genanki
import random
from pathlib import Path
from typing import List, Dict, Any
from utils.logger import logger

class AnkiPackageGenerator:
    """Gera arquivos .apkg estruturados seguindo os modelos nativos do Anki (Basic e Cloze)."""

    def __init__(self):
        # IDs aleatórios seguros para os modelos do Genanki
        self.basic_model_id = 1607392319
        self.cloze_model_id = 1607392320

    def _get_basic_model(self) -> genanki.Model:
        return genanki.Model(
            self.basic_model_id,
            'MedStudy Basic Model',
            fields=[
                {'name': 'Front'},
                {'name': 'Back'},
                {'name': 'ClinicalPearl'},
                {'name': 'Header'}
            ],
            templates=[
                {
                    'name': 'Card 1',
                    'qfmt': '<div style="font-family: Arial; font-size: 16px; color: #555;">{{Header}}</div><br><div style="font-family: Arial; font-size: 20px; font-weight: bold;">{{Front}}</div>',
                    'afmt': '{{FrontSide}}<hr id="answer"><div style="font-family: Arial; font-size: 18px;">{{Back}}</div><br><div style="background-color: #e6ffed; border-left: 4px solid #28a745; padding: 10px; margin-top: 10px; font-family: Arial; font-size: 14px;">💡 <b>Pérola Clínica:</b> {{ClinicalPearl}}</div>',
                },
            ],
            css='''
            .card {
                font-family: arial;
                font-size: 20px;
                text-align: left;
                color: #212529;
                background-color: #f8f9fa;
                padding: 20px;
            }
            ''')

    def _get_cloze_model(self) -> genanki.Model:
        return genanki.Model(
            self.cloze_model_id,
            'MedStudy Cloze Model',
            fields=[
                {'name': 'Text'},
                {'name': 'BackExtra'},
                {'name': 'Header'}
            ],
            templates=[
                {
                    'name': 'Cloze Card',
                    'qfmt': '<div style="font-family: Arial; font-size: 16px; color: #555;">{{Header}}</div><br><div style="font-family: Arial; font-size: 20px;">{{cloze:Text}}</div>',
                    'afmt': '<div style="font-family: Arial; font-size: 16px; color: #555;">{{Header}}</div><br><div style="font-family: Arial; font-size: 20px;">{{cloze:Text}}</div><br><div style="font-family: Arial; font-size: 18px; color: #0056b3;">{{BackExtra}}</div>',
                },
            ],
            css='''
            .card {
                font-family: arial;
                font-size: 20px;
                text-align: left;
                color: #212529;
                background-color: #f8f9fa;
                padding: 20px;
            }
            .cloze {
                font-weight: bold;
                color: #0066cc;
            }
            ''')

    def generate_apkg(self, cards_data: List[Dict[str, Any]], deck_name: str, output_path: Path) -> Path:
        deck_id = random.randrange(1 << 30, 1 << 31)
        deck = genanki.Deck(deck_id, deck_name)

        basic_model = self._get_basic_model()
        cloze_model = self._get_cloze_model()

        for card in cards_data:
            card_type = card.get("type", "basic").lower()
            header = card.get("header", deck_name)

            if "cloze" in card_type or "omissão" in card_type:
                text = card.get("text", card.get("front", ""))
                back_extra = card.get("back_extra", card.get("back", ""))
                note = genanki.Note(
                    model=cloze_model,
                    fields=[text, back_extra, header]
                )
            else:
                front = card.get("front", "")
                back = card.get("back", "")
                pearl = card.get("clinical_pearl", "Conceito fundamental para a prática médica.")
                note = genanki.Note(
                    model=basic_model,
                    fields=[front, back, pearl, header]
                )
            
            deck.add_note(note)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        genanki.Package(deck).write_to_file(str(output_path))
        logger.info(f"Baralho Anki gerado com sucesso em: {output_path}")
        return output_path

anki_generator = AnkiPackageGenerator()