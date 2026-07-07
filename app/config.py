"""
Configuration constants and startup setup for the TextExtract backend.

MAXIMUM QUALITY MODE - accuracy over speed.
300 pages may take 10-15 minutes. That's acceptable.
"""
import os
import pytesseract

_IS_RENDER = bool(os.getenv("RENDER"))

# File limitations
MAX_FILE_SIZE_MB: int = int(os.getenv("MAX_FILE_SIZE_MB", "100"))
ALLOWED_EXTENSIONS: set[str] = {
    ".pdf", ".docx", ".png", ".jpg", ".jpeg", ".tiff", ".bmp", ".webp"
}

# Tesseract config - MAXIMUM QUALITY
TESSERACT_CMD: str = os.getenv("TESSERACT_CMD", "tesseract")
TESSDATA_PREFIX: str = os.getenv(
    "TESSDATA_PREFIX",
    "/usr/share/tesseract-ocr/5/tessdata",
)

# OEM 1 = LSTM only (best for Devanagari), PSM 3 = fully automatic
# Added more parameters for better accuracy
DEFAULT_OCR_CONFIG: str = (
    r"--oem 1 --psm 3 "
    r"-c preserve_interword_spaces=1 "
    r"-c textord_tabfind_find_tables=1 "
    r"-c textord_heavy_nr=1 "
    r"-c tessedit_do_invert=0 "
    r"-c textord_min_linesize=2.5"
)

# MAXIMUM QUALITY DPI settings - no compromises
PDF_RENDER_DPI: int = int(os.getenv("PDF_RENDER_DPI", "400"))
PDF_RENDER_DPI_HIGH: int = int(os.getenv("PDF_RENDER_DPI_HIGH", "450"))
PDF_RETRY_CONFIDENCE: float = float(os.getenv("PDF_RETRY_CONFIDENCE", "75"))
HIGH_DPI_RETRY_MAX_PAGES: int = int(os.getenv("HIGH_DPI_RETRY_MAX_PAGES", "999"))

# Sanitization DPI - MAXIMUM for Devanagari clarity
SANITIZE_DPI: int = int(os.getenv("SANITIZE_DPI", "400"))
SANITIZE_DPI_MEDIUM: int = int(os.getenv("SANITIZE_DPI_MEDIUM", "380"))
SANITIZE_DPI_LARGE: int = int(os.getenv("SANITIZE_DPI_LARGE", "350"))

# JPEG quality - keep maximum to preserve text edges
SANITIZE_JPEG_QUALITY: int = int(os.getenv("SANITIZE_JPEG_QUALITY", "95"))
SANITIZE_JPEG_MIN_QUALITY: int = int(os.getenv("SANITIZE_JPEG_MIN_QUALITY", "92"))
SANITIZE_MAX_JPEG_BYTES: int = int(os.getenv("SANITIZE_MAX_JPEG_BYTES", "2000000"))

# Multiple PSM modes for retries - try different segmentation
OCR_PSM_PRIMARY: int = 3
OCR_PSM_RETRY: tuple[int, ...] = (6, 4, 11, 12)

# Gap threshold for tab-separated table columns during layout reconstruction
COLUMN_GAP_RATIO: float = float(os.getenv("COLUMN_GAP_RATIO", "1.3"))

# Retry thresholds - AGGRESSIVE retries for quality
OCR_GOOD_CONFIDENCE: float = float(os.getenv("OCR_GOOD_CONFIDENCE", "85"))
OCR_RETRY_CONFIDENCE: float = float(os.getenv("OCR_RETRY_CONFIDENCE", "75"))
OCR_MIN_WORDS: int = int(os.getenv("OCR_MIN_WORDS", "2"))

# Image dimensions - LARGER for better text clarity
MIN_OCR_DIMENSION: int = int(os.getenv("MIN_OCR_DIMENSION", "2800"))
MAX_OCR_DIMENSION: int = int(os.getenv("MAX_OCR_DIMENSION", "5000"))

# Sequential processing for stability
OCR_PAGE_WORKERS: int = 1
PARALLEL_PAGE_THRESHOLD: int = 1


def render_dpi_for_page_count(page_count: int) -> int:
    """High DPI for all documents - quality over speed."""
    # Even for large documents, use high DPI
    if page_count > 200:
        return 350
    if page_count > 100:
        return 380
    return PDF_RENDER_DPI


def is_fast_ocr_mode(page_count: int) -> bool:
    """NEVER use fast mode - always prioritize quality."""
    return False


def configure_tesseract() -> None:
    """Configure the Tesseract command path and tessdata location."""
    pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD
    if TESSDATA_PREFIX:
        os.environ["TESSDATA_PREFIX"] = TESSDATA_PREFIX
