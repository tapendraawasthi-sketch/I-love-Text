"""
Document-level OCR using the image-PDF sanitization pipeline.
"""
from __future__ import annotations

from typing import Any

from app.extract.raster_pipeline import ocr_via_image_pdf_pipeline


def ocr_document(
    document_bytes: bytes,
    filetype: str,
    lang: str,
    *,
    digital: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """OCR a PDF or DOCX through PDF → image → image-PDF → OCR."""
    del digital  # sanitization pipeline always uses rasterised pages
    return ocr_via_image_pdf_pipeline(document_bytes, filetype, lang)


def format_page_results(
    page_results: list[dict[str, Any]],
    pipeline_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    page_texts = [result["text"] for result in page_results]
    confidences = [result["mean_confidence"] for result in page_results]

    if len(page_texts) == 1:
        final_text = page_texts[0]
    else:
        final_text = "\n\n--- Page Break ---\n\n".join(page_texts)

    mean_confidence = (
        round(sum(confidences) / len(confidences), 2) if confidences else 0.0
    )

    meta = {
        "text": final_text,
        "pages": len(page_results),
        "method_per_page": ["image_pdf_ocr"] * len(page_results),
        "method": "image_pdf_ocr",
        "mean_confidence": mean_confidence,
        "had_legacy_fonts": False,
        "detected_fonts": [],
    }
    if pipeline_meta:
        meta.update(pipeline_meta)
    return meta
