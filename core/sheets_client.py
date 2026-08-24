import os
from typing import Optional
from utils.logger import logger
from dotenv import load_dotenv

load_dotenv()

class SheetsClient:
    """Cliente para interagir com a Planilha de Controle de Aulas no Google Sheets."""

    def __init__(self):
        # Aqui você pode colocar o ID da sua planilha do Google Sheets via .env ou direto
        self.spreadsheet_id = os.getenv("GOOGLE_SHEET_ID", "")

    def update_lesson_link(self, unit_code: str, lesson_name: str, notebook_url: str) -> bool:
        """Atualiza a linha correspondente à aula na planilha com o link do NotebookLM."""
        try:
            if not self.spreadsheet_id:
                logger.info(f"[Sheets SIMULADO] Aula '{lesson_name}' ({unit_code}) -> Link NotebookLM: {notebook_url}")
                # Quando configurar a credencial de serviço do Sheets, a lógica de append/update entra aqui.
                return True
            
            # Lógica real de integração com gspread / google-api-python-client pode ser ativada aqui
            return True
        except Exception as e:
            logger.error(f"Erro ao atualizar planilha do Google Sheets: {e}")
            return False

sheets_client = SheetsClient()