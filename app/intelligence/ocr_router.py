"""
Adaptive OCR Router — selects the optimal extraction strategy per region.

Instead of one-size-fits-all Tesseract, the router examines each
region's characteristics and dispatches to the most suitable engine:

    Digital Unicode text  → PyMuPDF direct extraction (no OCR needed)
    Legacy font text      → Direct extraction + font conversion
    Clean scanned text    → Tesseract with standard preprocessing
    Noisy/degraded scan   → Tesseract with aggressive preprocessing
    Table region          → PyMuPDF find_tables() + cell OCR
    Form fields           → Specialized form extraction

Addresses Problem 12 from the architectural critique.
"""
from __future__ import annotations

from typing import Any

import fitz
import numpy as np

from app.intelligence.document_intelligence import (
    PageIntelligence, PageRegion, PageType, RegionType,
)
from app.logging_config import get_logger

logger = get_logger("OCRRouter")


class ExtractionResult:
    """Result from any extraction method."""

    def __init__(
        self,
        text: str = "",
        confidence: float = 0.0,
        method: str = "unknown",
        char_confidences: list[float] | None = None,
        metadata: dict[str, Any] | None = None,
    ):
        self.text = text
        self.confidence = confidence
        self.method = method
        self.char_confidences = char_confidences or []
        self.metadata = metadata or {}


def extract_region(
    page: fitz.Page,
    region: PageRegion,
    page_intel: PageIntelligence,
    font_lookup: dict[str, Any] | None = None,
    lang: str = "nep+eng",
) -> ExtractionResult:
    """
    Extract text from a single region using the optimal strategy.

    The strategy is determined by the region type and page intelligence.
    """
    strategy = region.extraction_strategy

    if strategy == "skip" or region.region_type == RegionType.EMPTY:
        return ExtractionResult(text="", confidence=100.0, method="skip")

    if strategy == "table_extraction":
        return _extract_table_region(page, region, font_lookup)

    if strategy == "direct":
        return _extract_direct(page, region, font_lookup)

    if strategy == "direct_with_conversion":
        return _extract_direct_with_conversion(page, region, font_lookup)

    if strategy == "ocr_standard":
        return _extract_ocr(page, region, lang, aggressive=False)

    if strategy == "ocr_enhanced":
        return _extract_ocr(page, region, lang, aggressive=True)

    if strategy == "ocr_with_deskew":
        return _extract_ocr(page, region, lang, aggressive=True)

    if strategy == "hybrid":
        return _extract_hybrid(page, region, page_intel, font_lookup, lang)

    # Default: try direct first, fall back to OCR
    return _extract_hybrid(page, region, page_intel, font_lookup, lang)


def extract_page(
    page: fitz.Page,
    page_intel: PageIntelligence,
    font_lookup: dict[str, Any] | None = None,
    lang: str = "nep+eng",
) -> dict[str, Any]:
    """
    Extract all text from a page using per-region routing.

    Returns combined text with metadata about each region's extraction.
    """
    region_results: list[dict[str, Any]] = []
    all_text_parts: list[tuple[float, str]] = []  # (y_position, text)

    for region in sorted(page_intel.regions, key=lambda r: r.reading_order):
        result = extract_region(page, region, page_intel, font_lookup, lang)

        if result.text.strip():
            all_text_parts.append((region.bbox[1], result.text))
            region_results.append({
                "region_type": region.region_type.value,
                "bbox": region.bbox,
                "method": result.method,
                "confidence": result.confidence,
                "char_count": len(result.text.strip()),
            })

    # Sort by vertical position (top to bottom) and combine
    all_text_parts.sort(key=lambda x: x[0])
    combined_text = "\n\n".join(text for _, text in all_text_parts)

    confidences = [r["confidence"] for r in region_results if r["confidence"] > 0]
    mean_confidence = (
        sum(confidences) / len(confidences) if confidences else 0
    )

    return {
        "text": combined_text,
        "confidence": mean_confidence,
        "method": page_intel.recommended_strategy,
        "regions": region_results,
    }


def _extract_direct(
    page: fitz.Page,
    region: PageRegion,
    font_lookup: dict[str, Any] | None,
) -> ExtractionResult:
    """Extract text directly from PDF text layer."""
    try:
        clip = fitz.Rect(region.bbox)
        text = page.get_text("text", clip=clip, sort=True).strip()
        if text:
            return ExtractionResult(
                text=text,
                confidence=95.0,
                method="direct_unicode",
            )
    except Exception as e:
        logger.debug("Direct extraction failed: %s", e)

    return ExtractionResult(text="", confidence=0, method="direct_failed")


def _extract_direct_with_conversion(
    page: fitz.Page,
    region: PageRegion,
    font_lookup: dict[str, Any] | None,
) -> ExtractionResult:
    """Extract text and apply legacy font conversion."""
    from app.extract.direct_extract import extract_page_direct

    try:
        result = extract_page_direct(page, font_lookup)
        if result.get("text", "").strip():
            return ExtractionResult(
                text=result["text"],
                confidence=result.get("confidence", 80.0),
                method="direct_legacy_conversion",
                metadata={
                    "legacy_fonts": result.get("legacy_fonts", []),
                    "devanagari_ratio": result.get("devanagari_ratio", 0),
                },
            )
    except Exception as e:
        logger.debug("Direct conversion failed: %s", e)

    return ExtractionResult(text="", confidence=0, method="conversion_failed")


def _extract_ocr(
    page: fitz.Page,
    region: PageRegion,
    lang: str,
    aggressive: bool = False,
) -> ExtractionResult:
    """Extract text using OCR on the rendered page region."""
    import gc
    from app.extract.render import pixmap_to_bgr
    from app.ocr.preprocess import preprocess_for_ocr
    from app.ocr.engine import run_ocr_smart

    try:
        clip = fitz.Rect(region.bbox)
        # Expand clip slightly for context
        clip.x0 = max(0, clip.x0 - 5)
        clip.y0 = max(0, clip.y0 - 5)
        clip.x1 += 5
        clip.y1 += 5

        dpi = 350 if aggressive else 300
        mat = fitz.Matrix(dpi / 72.0, dpi / 72.0)
        pix = page.get_pixmap(matrix=mat, clip=clip, alpha=False)
        image_bgr = pixmap_to_bgr(pix)
        del pix

        processed = preprocess_for_ocr(
            image_bgr, aggressive=aggressive, digital=not aggressive
        )
        result = run_ocr_smart(processed, lang, fast=False)
        del image_bgr, processed
        gc.collect()

        return ExtractionResult(
            text=result.get("text", ""),
            confidence=result.get("mean_confidence", 0),
            method=f"ocr_{'aggressive' if aggressive else 'standard'}",
        )
    except Exception as e:
        logger.debug("OCR extraction failed: %s", e)
        return ExtractionResult(text="", confidence=0, method="ocr_failed")


def _extract_table_region(
    page: fitz.Page,
    region: PageRegion,
    font_lookup: dict[str, Any] | None,
) -> ExtractionResult:
    """Extract text from a table region."""
    from app.extract.table_extractor import extract_tables_from_page

    try:
        tables = extract_tables_from_page(page, font_lookup)
        if tables:
            # Find the table that best matches this region
            for table in tables:
                tbbox = table["bbox"]
                # Check overlap
                if (tbbox[0] < region.bbox[2] and tbbox[2] > region.bbox[0] and
                        tbbox[1] < region.bbox[3] and tbbox[3] > region.bbox[1]):
                    return ExtractionResult(
                        text=table["formatted"],
                        confidence=85.0,
                        method="table_extraction",
                        metadata={"rows": len(table["rows"])},
                    )
    except Exception as e:
        logger.debug("Table extraction failed: %s", e)

    # Fallback to direct extraction
    return _extract_direct(page, region, font_lookup)


def _extract_hybrid(
    page: fitz.Page,
    region: PageRegion,
    page_intel: PageIntelligence,
    font_lookup: dict[str, Any] | None,
    lang: str,
) -> ExtractionResult:
    """
    Hybrid extraction: try direct first, fall back to OCR.

    Uses the page intelligence to decide the best strategy.
    """
    # Try direct extraction first
    if page_intel.has_text_layer:
        if page_intel.legacy_fonts:
            direct_result = _extract_direct_with_conversion(
                page, region, font_lookup
            )
        else:
            direct_result = _extract_direct(page, region, font_lookup)

        if (direct_result.text.strip() and
                len(direct_result.text.strip()) >= 5 and
                direct_result.confidence >= 50):
            return direct_result

    # Fall back to OCR
    ocr_result = _extract_ocr(
        page, region, lang,
        aggressive=page_intel.page_type == PageType.SCANNED_NOISY,
    )

    return ocr_result
