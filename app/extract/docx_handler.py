"""
DOCX text extraction via precision hybrid pipeline.
"""
from __future__ import annotations

from typing import Any

from app.extract.document_ocr import format_page_results, ocr_document


def extract_docx(docx_bytes: bytes, lang: str = "auto") -> dict[str, Any]:
    """
    Extract text per page: Unicode layer → legacy font conversion → OCR fallback.
    """
    page_results, pipeline_meta = ocr_document(docx_bytes, "docx", lang)
    return format_page_results(page_results, pipeline_meta)
