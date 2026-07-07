"""
Document-level OCR with parallel pages and adaptive high-DPI retries.
"""
from __future__ import annotations

from typing import Any

import fitz
import numpy as np

from app.config import PDF_RENDER_DPI, PDF_RENDER_DPI_HIGH, PDF_RETRY_CONFIDENCE
from app.extract.ocr_pipeline import ocr_image, should_retry_page
from app.extract.page_ocr import ocr_page_images
from app.extract.render import render_page
from app.ocr.engine import score_result_dict


def _open_document(document_bytes: bytes, filetype: str) -> fitz.Document:
    try:
        return fitz.open(stream=document_bytes, filetype=filetype)
    except Exception as exc:
        raise ValueError(f"Failed to open {filetype.upper()} document: {exc}") from exc


def _render_all_pages(doc: fitz.Document, dpi: int) -> list[np.ndarray]:
    return [render_page(doc.load_page(index), dpi) for index in range(len(doc))]


def _retry_weak_pages(
    doc: fitz.Document,
    page_results: list[dict[str, Any]],
    lang: str,
    *,
    digital: bool,
) -> list[dict[str, Any]]:
    updated = list(page_results)

    for index, result in enumerate(page_results):
        weak = (
            should_retry_page(result)
            or result["mean_confidence"] < PDF_RETRY_CONFIDENCE
        )
        if not weak:
            continue

        high_res_image = render_page(doc.load_page(index), PDF_RENDER_DPI_HIGH)
        retry = ocr_image(high_res_image, lang, digital=digital)

        if score_result_dict(retry) > score_result_dict(result):
            updated[index] = retry

    return updated


def ocr_document(
    document_bytes: bytes,
    filetype: str,
    lang: str,
    *,
    digital: bool = True,
) -> list[dict[str, Any]]:
    """
    OCR every page in a document.

    Uses parallel workers for multi-page files and re-renders only weak pages
    at a higher DPI for better Nepali accuracy without slowing every page.
    """
    doc = _open_document(document_bytes, filetype)
    try:
        if len(doc) == 0:
            raise ValueError(f"No pages found in {filetype.upper()} document.")

        page_images = _render_all_pages(doc, PDF_RENDER_DPI)
        results = ocr_page_images(page_images, lang, digital=digital)
        return _retry_weak_pages(doc, results, lang, digital=digital)
    finally:
        doc.close()


def format_page_results(page_results: list[dict[str, Any]]) -> dict[str, Any]:
    page_texts = [result["text"] for result in page_results]
    confidences = [result["mean_confidence"] for result in page_results]

    if len(page_texts) == 1:
        final_text = page_texts[0]
    else:
        final_text = "\n\n--- Page Break ---\n\n".join(page_texts)

    mean_confidence = (
        round(sum(confidences) / len(confidences), 2) if confidences else 0.0
    )

    return {
        "text": final_text,
        "pages": len(page_results),
        "method_per_page": ["image_ocr"] * len(page_results),
        "method": "image_ocr",
        "mean_confidence": mean_confidence,
        "had_legacy_fonts": False,
        "detected_fonts": [],
    }
