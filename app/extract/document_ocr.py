"""
Document-level extraction with direct text + OCR fallback.
"""
from __future__ import annotations

from typing import Any

from app.extract.direct_extract import extract_document_direct, extract_page_direct
from app.extract.precision_pipeline import extract_page_precision
from app.logging_config import get_logger

import fitz

logger = get_logger("DocumentOCR")


def ocr_document(
    document_bytes: bytes,
    filetype: str,
    lang: str,
    *,
    digital: bool = True,
    mode: str = "auto",
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Extract text from document.
    
    Modes:
        - "direct": Only use text layer extraction (no OCR)
        - "ocr": Only use image OCR
        - "auto": Try direct first, OCR for pages with no text layer
    """
    if mode == "direct":
        return extract_document_direct(document_bytes, filetype)
    
    if mode == "ocr":
        from app.extract.precision_pipeline import extract_document_precision
        return extract_document_precision(document_bytes, filetype, lang)
    
    # Auto mode: direct first, OCR fallback for empty pages
    return _extract_auto_mode(document_bytes, filetype, lang)


def _extract_auto_mode(
    document_bytes: bytes,
    filetype: str,
    lang: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Smart extraction: direct text layer first, OCR only for pages without text.
    """
    try:
        doc = fitz.open(stream=document_bytes, filetype=filetype)
    except Exception as exc:
        raise ValueError(f"Failed to open document: {exc}") from exc
    
    page_count = len(doc)
    results: list[dict[str, Any]] = []
    methods: list[str] = []
    all_fonts: set[str] = set()
    legacy_fonts: set[str] = set()
    ocr_pages = 0
    direct_pages = 0
    
    try:
        for idx in range(page_count):
            page = doc.load_page(idx)
            
            # Try direct extraction first
            direct_result = extract_page_direct(page)
            
            if direct_result["method"] != "no_text" and direct_result["char_count"] >= 5:
                # Direct extraction successful
                results.append(direct_result)
                methods.append(direct_result["method"])
                all_fonts.update(direct_result.get("fonts", []))
                legacy_fonts.update(direct_result.get("legacy_fonts", []))
                direct_pages += 1
                
                if page_count < 20 or idx % 20 == 0:
                    logger.info(
                        "Page %d/%d: %s (direct, %.1f%% Devanagari)",
                        idx + 1, page_count,
                        direct_result["method"],
                        direct_result.get("devanagari_ratio", 0)
                    )
            else:
                # No text layer - use OCR
                ocr_result = extract_page_precision(page, lang)
                results.append(ocr_result)
                methods.append("image_ocr")
                ocr_pages += 1
                
                if page_count < 20 or idx % 20 == 0:
                    logger.info(
                        "Page %d/%d: image_ocr (no text layer)",
                        idx + 1, page_count
                    )
    finally:
        doc.close()
    
    meta = {
        "pipeline": "auto_direct_ocr",
        "total_pages": page_count,
        "direct_pages": direct_pages,
        "ocr_pages": ocr_pages,
        "method_per_page": methods,
        "all_fonts": sorted(all_fonts),
        "legacy_fonts_found": sorted(legacy_fonts),
    }
    
    return results, meta


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
