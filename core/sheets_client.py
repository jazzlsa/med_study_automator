import re
from pathlib import Path
from typing import Optional
from utils.logger import logger
from dotenv import load_dotenv

from config.settings import settings
from core.sheets_sync import AVAILABLE_UCS, AULA_KEYWORDS, LINK_KEYWORDS, AUTOR_KEYWORDS, find_column_by_keywords

load_dotenv()

# Preenchido na coluna "Feito por" só quando uma linha NOVA é acrescentada (nunca
# sobrescreve o que já estiver lá numa linha existente) - deixa claro que aquela
# aula foi processada pelo pipeline automático, não por uma pessoa manualmente.
AUTOMATED_AUTHOR_LABEL = "Jéssica (automatizado Gemini)"

try:
    import gspread
    GSPREAD_AVAILABLE = True
except ImportError:
    GSPREAD_AVAILABLE = False


class SheetsClient:
    """Cliente para interagir com a Planilha de Controle de Aulas no Google Sheets.

    Usa a API real (gspread + credencial de service account) quando
    GOOGLE_CREDENTIALS_PATH e GOOGLE_SPREADSHEET_ID (config/settings.py, vindos do
    .env) estão configurados e o arquivo de credencial existe. Caso contrário, cai
    no modo simulado (loga "[Sheets SIMULADO]" e retorna True) para não travar o
    pipeline quando a integração ainda não foi configurada.
    """

    def __init__(self):
        self.spreadsheet_id: Optional[str] = settings.secrets.GOOGLE_SPREADSHEET_ID
        self.credentials_path: Optional[Path] = settings.secrets.GOOGLE_CREDENTIALS_PATH
        self._spreadsheet = None  # cache da planilha autenticada (lazy)

        self.enabled = bool(
            GSPREAD_AVAILABLE
            and self.spreadsheet_id
            and self.credentials_path
            and Path(self.credentials_path).exists()
        )

        if not self.enabled:
            if not GSPREAD_AVAILABLE:
                reason = "biblioteca gspread não instalada"
            elif not self.spreadsheet_id:
                reason = "GOOGLE_SPREADSHEET_ID não configurado no .env"
            elif not self.credentials_path:
                reason = "GOOGLE_CREDENTIALS_PATH não configurado no .env"
            else:
                reason = f"arquivo de credencial não encontrado: {self.credentials_path}"
            logger.info(f"Integração real com Google Sheets desativada ({reason}); usando modo simulado.")

    def _get_spreadsheet(self):
        """Autentica (uma vez) via service account e retorna o Spreadsheet do gspread."""
        if self._spreadsheet is not None:
            return self._spreadsheet
        client = gspread.service_account(filename=str(self.credentials_path))
        self._spreadsheet = client.open_by_key(self.spreadsheet_id)
        return self._spreadsheet

    @staticmethod
    def _resolve_worksheet_title(unit_code: str) -> str:
        """Resolve o nome exato da aba a partir do unit_code, reaproveitando a mesma
        lista de nomes de AVAILABLE_UCS usada por core/sheets_sync.py.

        Caso conhecido: "UC21" não é uma aba sozinha - existem "UC21 - TURMA A" e
        "UC21 - TURMA B". Sem mais contexto pra escolher a turma certa, usamos a
        primeira como padrão em vez de quebrar (exceção conhecida, não é o foco
        desta etapa)."""
        if unit_code in AVAILABLE_UCS:
            return unit_code
        matches = [uc for uc in AVAILABLE_UCS if uc.startswith(unit_code)]
        if matches:
            return matches[0]
        return unit_code

    @staticmethod
    def _lesson_matches(cell_value: str, lesson_name: str) -> bool:
        """Compara o valor de uma célula da coluna de aula com o lesson_name recebido.

        A planilha costuma ter o título completo da aula (ex.: "Aula 6 - Infecções de
        Vias Aéreas Superiores"), enquanto lesson_name normalmente vem só como "Aula 6"
        (nome da pasta no Drive) - então, além do match exato, aceita a célula começando
        com lesson_name seguido de um separador (espaço, traço, dois-pontos), pra não
        confundir "Aula 6" com "Aula 60"."""
        cell_norm = cell_value.strip().lower()
        target_norm = lesson_name.strip().lower()
        if not cell_norm or not target_norm:
            return False
        if cell_norm == target_norm:
            return True
        if cell_norm.startswith(target_norm):
            rest = cell_norm[len(target_norm):]
            if not rest or rest[0] in (" ", "-", ":", "–", "—"):
                return True
        return False

    @staticmethod
    def _col_letter(col_idx: int) -> str:
        """Converte um índice de coluna 1-based (1, 2, 27...) pra letra A1 (A, B, AA...)."""
        return re.sub(r"\d+$", "", gspread.utils.rowcol_to_a1(1, col_idx))

    def _copy_row_background_color(self, worksheet, target_row: int, num_cols: int) -> None:
        """Copia a cor de fundo da linha imediatamente ACIMA (target_row - 1) para a
        linha nova (target_row), coluna a coluna - só nas colunas que realmente têm
        cor definida na linha de cima (na prática, normalmente só a coluna A).

        Faz sentido porque aulas novas adicionadas em sequência normalmente pertencem
        à mesma prova da aula anterior. Se não houver linha de dado acima (aba vazia,
        só o cabeçalho), não faz nada - fica sem cor mesmo, caso raro."""
        source_row = target_row - 1
        if source_row < 2:
            return
        try:
            last_col = self._col_letter(num_cols)
            a1_range = f"{worksheet.title}!A{source_row}:{last_col}{source_row}"
            meta = worksheet.spreadsheet.fetch_sheet_metadata(params={
                "ranges": [a1_range],
                "fields": "sheets.data.rowData.values.userEnteredFormat.backgroundColor",
                "includeGridData": "true",
            })
            row_data = meta["sheets"][0]["data"][0].get("rowData", [])
            if not row_data:
                return
            values = row_data[0].get("values", [])
            for idx, cell in enumerate(values):
                bg = cell.get("userEnteredFormat", {}).get("backgroundColor")
                if not bg:
                    continue
                col_letter = self._col_letter(idx + 1)
                worksheet.format(f"{col_letter}{target_row}", {"backgroundColor": bg})
            logger.info(f"Cor de fundo da linha {source_row} copiada para a linha nova {target_row}.")
        except Exception as e:
            # Cosmético - nunca deve derrubar o registro da aula por causa disso.
            logger.warning(f"Não consegui copiar a cor da linha acima para a linha {target_row}: {e}")

    def update_lesson_link(
        self, unit_code: str, lesson_name: str, notebook_url: str, tema: Optional[str] = None
    ) -> bool:
        """Escreve notebook_url na linha da aula correspondente, dentro da aba do
        unit_code. Se a linha já existir (reprocessamento), só atualiza a célula de
        link; se não existir (caso normal - aulas são sempre novas), acrescenta uma
        linha nova no final com o nome da aula (+ " - {tema}" quando disponível) e o
        link, copiando a cor de fundo da linha de cima. Retorna False (sem levantar
        exceção nem travar o pipeline) se a integração não estiver configurada, ou se
        a aba/colunas correspondentes não forem encontradas."""
        if not self.enabled:
            logger.info(f"[Sheets SIMULADO] Aula '{lesson_name}' ({unit_code}) -> Link NotebookLM: {notebook_url}")
            return True

        try:
            spreadsheet = self._get_spreadsheet()
        except Exception as e:
            logger.error(f"Falha ao autenticar/abrir a planilha do Google Sheets: {e}")
            return False

        worksheet_title = self._resolve_worksheet_title(unit_code)
        try:
            worksheet = spreadsheet.worksheet(worksheet_title)
        except Exception as e:
            logger.warning(
                f"Não encontrei a aba '{worksheet_title}' na planilha para a unidade '{unit_code}': {e}"
            )
            return False

        try:
            headers = worksheet.row_values(1)
        except Exception as e:
            logger.error(f"Falha ao ler o cabeçalho da aba '{worksheet_title}': {e}")
            return False

        # A coluna de aula é achada por keyword no cabeçalho (ex.: "Aula", "Tema") -
        # ou, se nenhuma bater, cai pra coluna A por posição (bug real corrigido:
        # abas como 'UC17' têm a coluna A funcionando normalmente como coluna de
        # aula, mas com o CABEÇALHO em branco - antes disso era tratado por engano
        # como "coluna não encontrada" só porque o texto do cabeçalho era "").
        # aula_col_idx é sempre um índice (1-based), nunca reobtido via
        # headers.index(texto) - isso evitava um segundo bug: '' aparece mais de
        # uma vez em headers (ex.: colunas A e D ambas em branco), então
        # headers.index('') sempre acharia a primeira ocorrência (correto aqui só
        # por coincidência de posição, não por design).
        aula_col_name = find_column_by_keywords(headers, AULA_KEYWORDS)
        aula_col_idx = headers.index(aula_col_name) + 1 if aula_col_name else (1 if headers else None)

        link_col_name = find_column_by_keywords(headers, LINK_KEYWORDS)
        link_col_idx = headers.index(link_col_name) + 1 if link_col_name else None

        if not aula_col_idx or not link_col_idx:
            logger.warning(
                f"Não encontrei as colunas de aula/link no cabeçalho da aba '{worksheet_title}': {headers}"
            )
            return False

        # "Feito por" é opcional - nem toda aba tem essa coluna, e sua ausência não
        # deve impedir o registro do link.
        autor_col_name = find_column_by_keywords(headers, AUTOR_KEYWORDS)
        autor_col_idx = headers.index(autor_col_name) + 1 if autor_col_name else None

        try:
            aula_values = worksheet.col_values(aula_col_idx)
        except Exception as e:
            logger.error(f"Falha ao ler a coluna de aulas na aba '{worksheet_title}': {e}")
            return False

        target_row = next(
            (
                idx
                for idx, value in enumerate(aula_values[1:], start=2)  # pula o cabeçalho
                if self._lesson_matches(value, lesson_name)
            ),
            None,
        )

        if target_row is not None:
            # Linha já existe (reprocessamento): só atualiza a célula de link.
            try:
                worksheet.update_cell(target_row, link_col_idx, notebook_url)
            except Exception as e:
                logger.error(
                    f"Falha ao escrever o link do NotebookLM na planilha (aba '{worksheet_title}', linha {target_row}): {e}"
                )
                return False

            logger.info(
                f"Link do NotebookLM atualizado na planilha: aba '{worksheet_title}', linha {target_row} -> {notebook_url}"
            )
            return True

        # Caso normal: aula nova, ainda sem linha na planilha - acrescenta no final.
        # Nome da linha inclui o tema real do slide quando disponível (ex.: "Aula 7 -
        # Caso 1 - Pneumonia bacteriana"), sem inventar nem resumir nada. Pula o tema
        # se ele já estiver contido no nome da pasta (ex.: tema "Caso 1" dentro de
        # "Aula 7 - Caso 1" - ficaria redundante, tipo slides de caso clínico sem
        # título diagnóstico próprio, só a identificação do caso).
        has_real_tema = bool(tema) and tema.strip().lower() not in lesson_name.lower()
        row_lesson_name = f"{lesson_name} - {tema}" if has_real_tema else lesson_name
        new_row = len(aula_values) + 1
        try:
            worksheet.update_cell(new_row, aula_col_idx, row_lesson_name)
            worksheet.update_cell(new_row, link_col_idx, notebook_url)
            if autor_col_idx:
                worksheet.update_cell(new_row, autor_col_idx, AUTOMATED_AUTHOR_LABEL)
        except Exception as e:
            logger.error(
                f"Falha ao acrescentar a aula '{row_lesson_name}' na planilha (aba '{worksheet_title}', linha {new_row}): {e}"
            )
            return False

        self._copy_row_background_color(worksheet, new_row, len(headers))

        logger.info(
            f"Aula '{row_lesson_name}' acrescentada como linha nova na planilha: "
            f"aba '{worksheet_title}', linha {new_row} -> {notebook_url}"
        )
        return True


sheets_client = SheetsClient()
