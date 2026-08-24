from pathlib import Path
from typing import List
import fitz  # PyMuPDF
from config.settings import settings
from utils.logger import logger


class SlideExtractor:
    """Extrai páginas de slides em PDF como imagens PNG para anexar a flashcards."""

    def __init__(self, output_base_dir: Path = settings.storage.temp_dir):
        self.output_base_dir = output_base_dir
        self.output_base_dir.mkdir(parents=True, exist_ok=True)

    def extract_slide_pages(
        self, pdf_path: Path, lesson_name: str, dpi: int = 150
    ) -> List[Path]:
        """Renderiza cada página do PDF como uma imagem PNG de alta qualidade."""
        pdf_path = Path(pdf_path)
        if not pdf_path.exists():
            raise FileNotFoundError(f"Arquivo de slide não encontrado: {pdf_path}")

        lesson_images_dir = self.output_base_dir / "slides" / lesson_name
        lesson_images_dir.mkdir(parents=True, exist_ok=True)

        doc = fitz.open(pdf_path)
        image_paths: List[Path] = []
        zoom = dpi / 72  # Fator de escala padrão de 72 DPI
        matrix = fitz.Matrix(zoom, zoom)

        logger.info(f"Renderizando {len(doc)} páginas do slide: {pdf_path.name}")

        for page_num in range(len(doc)):
            page = doc.load_page(page_num)
            pixmap = page.get_pixmap(matrix=matrix, alpha=False)
            output_file = lesson_images_dir / f"slide_page_{page_num + 1:03d}.png"
            pixmap.save(output_file)
            image_paths.append(output_file)

        doc.close()
        logger.debug(f"{len(image_paths)} imagens de slides salvas em {lesson_images_dir}")
        return image_paths


slide_extractor = SlideExtractor()