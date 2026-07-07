"""
PDF text extraction via image-first OCR.
"""
from __future__ import annotations

from typing import Any

from app.extract.document_ocr import format_page_results, ocr_document


def extract_pdf(pdf_bytes: bytes, lang: str = "auto") -> dict[str, Any]:
    """Extract text from PDF using image-first OCR on every page."""
    page_results = ocr_document(pdf_bytes, "pdf", lang, digital=True)
    return format_page_results(page_results)
