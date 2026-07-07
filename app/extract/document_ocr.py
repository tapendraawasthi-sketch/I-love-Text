"""
Document-level extraction using the precision hybrid pipeline.
"""
from __future__ import annotations

from typing import Any

from app.extract.precision_pipeline import extract_document_precision


def ocr_document(
    document_bytes: bytes,
    filetype: str,
    lang: str,
    *,
    digital: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Extract text per page: digital layer → legacy conversion → OCR fallback."""
    del digital
    return extract_document_precision(document_bytes, filetype, lang)


def format_page_results(
    page_results: list[dict[str, Any]],
    pipeline_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    page_texts = [result["text"] for result in page_results]
    confidences = [
        result.get("confidence", result.get("mean_confidence", 0))
        for result in page_results
    ]
    methods = [result.get("method", "unknown") for result in page_results]

    if len(page_texts) == 1:
        final_text = page_texts[0]
    else:
        final_text = "\n\n--- Page Break ---\n\n".join(page_texts)

    mean_confidence = (
        round(sum(confidences) / len(confidences), 2) if confidences else 0.0
    )
    digital_pages = sum(1 for m in methods if str(m).startswith("digital"))

    meta = {
        "text": final_text,
        "pages": len(page_results),
        "method_per_page": methods,
        "method": "precision_hybrid",
        "mean_confidence": mean_confidence,
        "digital_pages": digital_pages,
        "ocr_pages": len(page_results) - digital_pages,
        "had_legacy_fonts": any(m == "digital_legacy" for m in methods),
    }
    if pipeline_meta:
        meta.update(pipeline_meta)
    return meta
