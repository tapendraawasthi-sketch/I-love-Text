"""
DOCX text extraction via direct text layer + OCR fallback.
"""
from __future__ import annotations

from typing import Any

from app.extract.document_ocr import format_page_results, ocr_document


def extract_docx(docx_bytes: bytes, lang: str = "auto", mode: str = "auto") -> dict[str, Any]:
    """
    Extract text from DOCX.
    
    Modes:
        - "direct": Text layer only (95-100% accuracy for digital docs)
        - "ocr": Image OCR only
        - "auto": Direct first, OCR for pages without text
    """
    page_results, pipeline_meta = ocr_document(docx_bytes, "docx", lang, mode=mode)
    return format_page_results(page_results, pipeline_meta)
