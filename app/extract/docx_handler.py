"""
DOCX text extraction via bias-free image-PDF sanitization pipeline.
"""
from __future__ import annotations

from typing import Any

from app.extract.document_ocr import format_page_results, ocr_document


def extract_docx(docx_bytes: bytes, lang: str = "auto") -> dict[str, Any]:
    """
    Extract text from DOCX:
    DOCX → images → image-only PDF → OCR (no legacy font text layer).
    """
    page_results, pipeline_meta = ocr_document(docx_bytes, "docx", lang)
    return format_page_results(page_results, pipeline_meta)
