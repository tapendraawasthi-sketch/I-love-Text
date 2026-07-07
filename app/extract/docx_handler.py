"""
DOCX text extraction via image-first OCR.
"""
from __future__ import annotations

from typing import Any

from app.extract.document_ocr import format_page_results, ocr_document


def extract_docx(docx_bytes: bytes, lang: str = "auto") -> dict[str, Any]:
    """Extract text from DOCX by rendering each page to an image and running OCR."""
    page_results = ocr_document(docx_bytes, "docx", lang, digital=True)
    return format_page_results(page_results)
