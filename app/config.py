"""
Configuration constants and startup setup for the TextExtract backend.
"""
import os
import pytesseract

# File limitations
MAX_FILE_SIZE_MB: int = 25
ALLOWED_EXTENSIONS: set[str] = {
    ".pdf", ".docx", ".png", ".jpg", ".jpeg", ".tiff", ".bmp", ".webp"
}

# Tesseract config
TESSERACT_CMD: str = os.getenv("TESSERACT_CMD", "tesseract")

# OEM 1 = LSTM only (best for Devanagari), PSM 3 = fully automatic
DEFAULT_OCR_CONFIG: str = (
    r'--oem 1 --psm 3 '
    r'-c preserve_interword_spaces=1 '
    r'-c textord_tabfind_find_tables=1'
)

# Higher DPI for PDF/DOCX-to-image rendering (better OCR accuracy)
PDF_RENDER_DPI: int = 300

# Page segmentation modes to try for table-heavy Nepali documents
OCR_PSM_CANDIDATES: tuple[int, ...] = (3, 4, 6)

# Gap threshold for tab-separated table columns during layout reconstruction
COLUMN_GAP_RATIO: float = 1.5


def configure_tesseract() -> None:
    """Configure the Tesseract command path."""
    pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD
