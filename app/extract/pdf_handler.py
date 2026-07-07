"""
PDF text extraction via bias-free image-PDF sanitization pipeline.
"""
from __future__ import annotations

from typing import Any

from app.extract.document_ocr import format_page_results, ocr_document


def extract_pdf(pdf_bytes: bytes, lang: str = "auto") -> dict[str, Any]:
    """
    Extract text from PDF:
    PDF → images → image-only PDF → OCR (no Preeti/Kantipur text layer).
    """
    page_results, pipeline_meta = ocr_document(pdf_bytes, "pdf", lang)
    return format_page_results(page_results, pipeline_meta)
