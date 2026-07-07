"""
PDF text extraction via direct text layer + OCR fallback.
"""
from __future__ import annotations

from typing import Any

from app.extract.document_ocr import format_page_results, ocr_document


def extract_pdf(pdf_bytes: bytes, lang: str = "auto", mode: str = "auto") -> dict[str, Any]:
    """
    Extract text from PDF.
    
    Modes:
        - "direct": Text layer only (95-100% accuracy for digital PDFs)
        - "ocr": Image OCR only
        - "auto": Direct first, OCR for pages without text
    """
    page_results, pipeline_meta = ocr_document(pdf_bytes, "pdf", lang, mode=mode)
    return format_page_results(page_results, pipeline_meta)
