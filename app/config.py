"""
Configuration constants and startup setup for the TextExtract backend.
"""
import os
import pytesseract

# File limitations
MAX_FILE_SIZE_MB: int = int(os.getenv("MAX_FILE_SIZE_MB", "25"))
ALLOWED_EXTENSIONS: set[str] = {
    ".pdf", ".docx", ".png", ".jpg", ".jpeg", ".tiff", ".bmp", ".webp"
}

# Tesseract config
TESSERACT_CMD: str = os.getenv("TESSERACT_CMD", "tesseract")
TESSDATA_PREFIX: str = os.getenv(
    "TESSDATA_PREFIX",
    "/usr/share/tesseract-ocr/5/tessdata",
)

# OEM 1 = LSTM only (best for Devanagari), PSM 3 = fully automatic
DEFAULT_OCR_CONFIG: str = (
    r"--oem 1 --psm 3 "
    r"-c preserve_interword_spaces=1 "
    r"-c textord_tabfind_find_tables=1 "
    r"-c textord_heavy_nr=1"
)

# Primary render DPI; high-DPI retry is used only for weak pages.
PDF_RENDER_DPI: int = int(os.getenv("PDF_RENDER_DPI", "340"))
PDF_RENDER_DPI_HIGH: int = int(os.getenv("PDF_RENDER_DPI_HIGH", "420"))
PDF_RETRY_CONFIDENCE: float = float(os.getenv("PDF_RETRY_CONFIDENCE", "62"))

# Fast path uses one PSM; retries only add modes when confidence is weak.
OCR_PSM_PRIMARY: int = 3
OCR_PSM_RETRY: tuple[int, ...] = (4, 6)

# Gap threshold for tab-separated table columns during layout reconstruction
COLUMN_GAP_RATIO: float = float(os.getenv("COLUMN_GAP_RATIO", "1.5"))

# Retry thresholds
OCR_GOOD_CONFIDENCE: float = float(os.getenv("OCR_GOOD_CONFIDENCE", "70"))
OCR_RETRY_CONFIDENCE: float = float(os.getenv("OCR_RETRY_CONFIDENCE", "60"))
OCR_MIN_WORDS: int = int(os.getenv("OCR_MIN_WORDS", "4"))

# Image normalization keeps OCR fast on huge pages while preserving small text.
MIN_OCR_DIMENSION: int = int(os.getenv("MIN_OCR_DIMENSION", "2200"))
MAX_OCR_DIMENSION: int = int(os.getenv("MAX_OCR_DIMENSION", "3800"))

# Parallel OCR for multi-page PDFs/DOCX (Tesseract runs as subprocess per page).
OCR_PAGE_WORKERS: int = int(
    os.getenv(
        "OCR_PAGE_WORKERS",
        str(min(4, os.cpu_count() or 2)),
    )
)


def configure_tesseract() -> None:
    """Configure the Tesseract command path and tessdata location."""
    pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD
    if TESSDATA_PREFIX:
        os.environ["TESSDATA_PREFIX"] = TESSDATA_PREFIX
