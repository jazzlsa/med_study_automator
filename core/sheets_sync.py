import io
import urllib.parse
from typing import Any, Dict, List, Optional, Sequence
import pandas as pd
import requests

from config.settings import settings
from utils.logger import logger

# config/config.yaml (bloco "semester") é a fonte de verdade pra tudo que muda de
# semestre pra semestre - editável pela aba "⚙️ Configurações" do app.py, sem
# precisar mexer em código. settings.secrets.GOOGLE_SPREADSHEET_ID (.env) e o ID
# fixo abaixo continuam como fallback, só pra não quebrar quem ainda não migrou
# pra usar o config.yaml.
SPREADSHEET_ID = (
    settings.semester.spreadsheet_id
    or settings.secrets.GOOGLE_SPREADSHEET_ID
    or "1K0ubSbGSSzmuVIHgI2l9d5TYDvjcHkTTJLmVO25WabM"
)

# Lista de abas mapeadas da planilha do semestre atual (config/config.yaml).
AVAILABLE_UCS = settings.semester.available_ucs or [
    "UC29",
    "UC17",
    "UC04",
    "UC05",
    "UC11",
    "UC16",
    "UC08",
    "UC06",
    "UC09",
    "UC10",
    "UC21 - TURMA A",
    "UC21 - TURMA B",
    "UC24",
]

# Palavras-chave usadas para achar as colunas de aula/link/autor no cabeçalho de
# cada aba. Reaproveitadas por core/sheets_client.py para ficar consistente com a
# leitura aqui.
AULA_KEYWORDS = ["aula", "tema", "título"]
LINK_KEYWORDS = ["link", "notebook", "nlm"]
AUTOR_KEYWORDS = ["feito por", "autor"]


def find_column_by_keywords(headers: Sequence[str], keywords: List[str]) -> Optional[str]:
    """Retorna o primeiro cabeçalho que contém alguma das keywords (case-insensitive),
    ou None se nenhum bater."""
    return next(
        (h for h in headers if any(k in str(h).lower() for k in keywords)),
        None,
    )


class SheetsSyncManager:
  """Gerencia a leitura das abas de UCs e extração das aulas diretamente da planilha."""

  def __init__(self, spreadsheet_id: str = SPREADSHEET_ID):
    self.spreadsheet_id = spreadsheet_id

  def fetch_lessons_for_uc(self, uc_name: str) -> List[Dict[str, Any]]:
    """Busca as aulas de uma UC específica via exportação de CSV do Google Sheets."""
    encoded_title = urllib.parse.quote(uc_name)
    url = f"https://docs.google.com/spreadsheets/d/{self.spreadsheet_id}/gviz/tq?tqx=out:csv&sheet={encoded_title}"

    try:
      response = requests.get(url, timeout=10)
      if response.status_code != 200:
        logger.warning(
            f"Erro ao buscar {uc_name} via GViz (Status {response.status_code})"
        )
        return []

      content = response.text
      if "<html" in content.lower() or "google.visualization" not in response.headers.get(
          "content-disposition", ""
      ) and len(content) < 50:
        logger.warning(f"Resposta inválida para aba {uc_name}")
        return []

      df = pd.read_csv(io.StringIO(content))
      df.columns = [str(c).strip() for c in df.columns]

      # Localiza colunas
      aula_col = find_column_by_keywords(df.columns, AULA_KEYWORDS) or (
          df.columns[0] if len(df.columns) > 0 else None
      )
      link_col = find_column_by_keywords(df.columns, LINK_KEYWORDS)
      autor_col = find_column_by_keywords(df.columns, AUTOR_KEYWORDS)

      lessons = []
      if aula_col:
        for _, row in df.iterrows():
          aula_val = str(row.get(aula_col, "")).strip()
          link_val = (
              str(row.get(link_col, "")).strip()
              if link_col and link_col in row
              else ""
          )
          autor_val = (
              str(row.get(autor_col, "")).strip()
              if autor_col and autor_col in row
              else ""
          )

          if (
              aula_val
              and aula_val.lower() not in ["nan", "none", "", "aula", "tema"]
              and not aula_val.startswith("Unnamed")
          ):
            lessons.append({
                "lesson_name": aula_val,
                "notebooklm_link": link_val
                if link_val.lower() not in ["nan", "none"]
                else "",
                "author": autor_val
                if autor_val.lower() not in ["nan", "none"]
                else "",
            })
      return lessons
    except Exception as e:
      logger.error(f"Erro ao buscar aulas da UC {uc_name}: {e}")
      return []


sheets_sync = SheetsSyncManager()