"""
Configuration constants and startup setup for the TextExtract backend.
"""
import os
import pytesseract

_IS_RENDER = bool(os.getenv("RENDER"))

# File limitations
MAX_FILE_SIZE_MB: int = int(os.getenv("MAX_FILE_SIZE_MB", "25"))
MAX_PDF_PAGES: int = int(os.getenv("MAX_PDF_PAGES", "80"))
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

# Render free tier: lower DPI + sequential pages avoids OOM (502 errors).
PDF_RENDER_DPI: int = int(os.getenv("PDF_RENDER_DPI", "300" if _IS_RENDER else "340"))
PDF_RENDER_DPI_HIGH: int = int(os.getenv("PDF_RENDER_DPI_HIGH", "380" if _IS_RENDER else "420"))
PDF_RETRY_CONFIDENCE: float = float(os.getenv("PDF_RETRY_CONFIDENCE", "62"))
HIGH_DPI_RETRY_MAX_PAGES: int = int(os.getenv("HIGH_DPI_RETRY_MAX_PAGES", "15"))

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
MIN_OCR_DIMENSION: int = int(os.getenv("MIN_OCR_DIMENSION", "2000" if _IS_RENDER else "2200"))
MAX_OCR_DIMENSION: int = int(os.getenv("MAX_OCR_DIMENSION", "3200" if _IS_RENDER else "3800"))

# On Render free (512MB), parallel Tesseract workers cause OOM → 502.
_DEFAULT_WORKERS = "1" if _IS_RENDER else str(min(2, os.cpu_count() or 2))
OCR_PAGE_WORKERS: int = int(os.getenv("OCR_PAGE_WORKERS", _DEFAULT_WORKERS))

# Only parallelize very small documents.
PARALLEL_PAGE_THRESHOLD: int = int(os.getenv("PARALLEL_PAGE_THRESHOLD", "2"))


def configure_tesseract() -> None:
    """Configure the Tesseract command path and tessdata location."""
    pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD
    if TESSDATA_PREFIX:
        os.environ["TESSDATA_PREFIX"] = TESSDATA_PREFIX
