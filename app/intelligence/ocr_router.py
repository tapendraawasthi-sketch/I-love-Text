"""
Block OCR Router — per-block extraction decisions.

Each semantic block is routed independently through a fixed decision tree.
Results are merged in reading order. Whole-page OCR runs only when every
block on the page is image-based (no extractable text layer).
"""
from __future__ import annotations

import gc
from typing import Any

import cv2
import fitz
import numpy as np

from app.intelligence.document_intelligence import PageIntelligence, PageRegion
from app.legacy_fonts.mappings import is_legacy_font
from app.logging_config import get_logger
from app.ocr.image_analysis import analyze_image_block, is_ocr_eligible_block

logger = get_logger("BlockRouter")

# Placeholders for non-text blocks
_PLACEHOLDER_SIGNATURE = "[Signature]"
_PLACEHOLDER_STAMP = "[Official Stamp]"
_PLACEHOLDER_FIGURE = "[Figure]"
_PLACEHOLDER_CHART = "[Chart]"
_PLACEHOLDER_IMAGE = "[Image]"
_PLACEHOLDER_BARCODE = "[Barcode]"


class ExtractionResult:
    """Result from a single block extraction."""

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


def extract_page(
    page: fitz.Page,
    page_intel: PageIntelligence,
    font_lookup: dict[str, Any] | None = None,
    lang: str = "nep+eng",
    *,
    force_ocr: bool = False,
) -> dict[str, Any]:
    """
    Extract a page block-by-block and merge in reading order.

    Whole-page OCR is used only when every block is image-based.
    """
    blocks = sorted(page_intel.regions, key=lambda r: r.reading_order)
    if not blocks:
        return {
            "text": "",
            "confidence": 0.0,
            "method": "no_blocks",
            "regions": [],
        }

    if not force_ocr and _all_blocks_image_based(blocks):
        return _extract_whole_page_ocr(page, lang, page_intel)

    block_results: list[dict[str, Any]] = []
    text_parts: list[tuple[int, str]] = []

    for region in blocks:
        result = extract_block(page, region, page_intel, font_lookup, lang, force_ocr=force_ocr)
        meta = region.metadata or {}
        block_id = meta.get("block_id", "")
        block_type = meta.get("block_type", region.region_type.value)

        entry = {
            "block_id": block_id,
            "block_type": block_type,
            "bbox": list(region.bbox),
            "reading_order": region.reading_order,
            "method": result.method,
            "confidence": result.confidence,
            "font_confidence": result.metadata.get("font_confidence", 0.0),
            "char_count": len(result.text.strip()),
            "route": result.metadata.get("route", result.method),
            "fonts": result.metadata.get("fonts", []),
            "is_mixed_fonts": result.metadata.get("is_mixed_fonts", False),
            "encoding": result.metadata.get("encoding", "unknown"),
            "ocr_confidence": result.metadata.get("ocr_confidence", 0.0),
            "image_quality": result.metadata.get("image_quality"),
        }
        block_results.append(entry)

        if result.text.strip():
            text_parts.append((region.reading_order, result.text))

    text_parts.sort(key=lambda x: x[0])
    combined = "\n\n".join(t for _, t in text_parts)

    confidences = [r["confidence"] for r in block_results if r["confidence"] > 0]
    mean_conf = sum(confidences) / len(confidences) if confidences else 0.0

    return {
        "text": combined,
        "confidence": mean_conf,
        "method": "block_router",
        "regions": block_results,
        "block_count": len(block_results),
    }


def extract_block(
    page: fitz.Page,
    region: PageRegion,
    page_intel: PageIntelligence,
    font_lookup: dict[str, Any] | None,
    lang: str,
    *,
    force_ocr: bool = False,
) -> ExtractionResult:
    """Route and extract a single semantic block."""
    route = _decide_route(region, force_ocr=force_ocr)
    meta = dict(region.metadata or {})
    meta["route"] = route
    bbox = region.bbox

    if route == "placeholder_signature":
        from app.extract.fidelity import allow_placeholders
        if not allow_placeholders():
            return ExtractionResult("", 100.0, "skipped_placeholder", metadata=meta)
        return ExtractionResult(_PLACEHOLDER_SIGNATURE, 100.0, "placeholder", metadata=meta)

    if route == "placeholder_stamp":
        from app.extract.fidelity import allow_placeholders
        if not allow_placeholders():
            return ExtractionResult("", 100.0, "skipped_placeholder", metadata=meta)
        return ExtractionResult(_PLACEHOLDER_STAMP, 100.0, "placeholder", metadata=meta)

    if route == "placeholder_figure":
        from app.extract.fidelity import allow_placeholders
        if not allow_placeholders():
            return ExtractionResult("", 100.0, "skipped_placeholder", metadata=meta)
        return ExtractionResult(_PLACEHOLDER_FIGURE, 100.0, "placeholder", metadata=meta)

    if route == "placeholder_chart":
        from app.extract.fidelity import allow_placeholders
        if not allow_placeholders():
            return ExtractionResult("", 100.0, "skipped_placeholder", metadata=meta)
        return ExtractionResult(_PLACEHOLDER_CHART, 100.0, "placeholder", metadata=meta)

    if route == "placeholder_image":
        from app.extract.fidelity import allow_placeholders
        if not allow_placeholders():
            return ExtractionResult("", 100.0, "skipped_placeholder", metadata=meta)
        return ExtractionResult(_PLACEHOLDER_IMAGE, 100.0, "placeholder", metadata=meta)

    if route == "qr_decode":
        return _extract_qr(page, bbox, meta)

    if route == "barcode_decode":
        return _extract_barcode(page, bbox, meta)

    if route == "table_extraction":
        return _extract_table(page, bbox, font_lookup, meta)

    if route == "legacy_conversion":
        return _extract_legacy(page, bbox, font_lookup, meta)

    if route == "pdf_extraction":
        return _extract_unicode(page, bbox, font_lookup, meta)

    if route == "ocr":
        return _extract_ocr_block(page, bbox, lang, page_intel, meta)

    return ExtractionResult("", 0.0, "skipped", metadata=meta)


# Backward-compatible alias
def extract_region(
    page: fitz.Page,
    region: PageRegion,
    page_intel: PageIntelligence,
    font_lookup: dict[str, Any] | None = None,
    lang: str = "nep+eng",
) -> ExtractionResult:
    return extract_block(page, region, page_intel, font_lookup, lang)


def _block_meta(region: PageRegion) -> dict[str, Any]:
    return region.metadata or {}


def _block_type(region: PageRegion) -> str:
    meta = _block_meta(region)
    return meta.get("block_type") or region.region_type.value


def _decide_route(region: PageRegion, *, force_ocr: bool = False) -> str:
    """
    Per-block decision tree (first match wins).

    1. Embedded Unicode  → PDF extraction
    2. Legacy font        → Legacy conversion
    3. Scanned text       → OCR
    4. Table              → Table extraction
    5. QR                 → QR decoder
    6. Barcode            → Barcode decoder
    7. Signature          → [Signature]
    8. Stamp              → [Official Stamp]
    9. Figure             → Figure placeholder
    10. Chart             → Chart placeholder
    """
    meta = _block_meta(region)
    btype = _block_type(region)
    contains_text = bool(meta.get("contains_text"))
    contains_image = bool(meta.get("contains_image"))
    contains_table = bool(meta.get("contains_table"))
    encoding = meta.get("encoding", "unknown")
    is_mixed = bool(meta.get("is_mixed_fonts"))
    has_legacy = bool(meta.get("has_legacy_font")) or any(
        f.get("is_legacy") for f in meta.get("fonts", [])
    )
    has_unicode = bool(meta.get("has_unicode_font")) or encoding in ("unicode", "mixed")
    font = meta.get("font") or ""

    if force_ocr and is_ocr_eligible_block(meta, btype):
        return "ocr"

    # Never OCR: signatures, QR, barcodes, vector figures/charts/formulas
    if btype == "signature":
        return "placeholder_signature"
    if btype == "qr_code":
        return "qr_decode"
    if btype == "barcode":
        return "barcode_decode"
    if btype in ("figure", "chart", "formula"):
        if btype == "figure":
            return "placeholder_figure"
        if btype == "chart":
            return "placeholder_chart"
        return "skipped"

    # Mixed fonts — per-span routing inside PDF extraction (Unicode never converted)
    if is_mixed or encoding == "mixed":
        return "pdf_extraction"

    # 1. Embedded Unicode — digital text layer (never OCR)
    if contains_text and not contains_image:
        if encoding == "unicode" or (has_unicode and not has_legacy):
            return "pdf_extraction"
        if not font or not is_legacy_font(font):
            if not has_legacy:
                return "pdf_extraction"

    # 2. Legacy font (never OCR)
    if has_legacy or (font and is_legacy_font(font)):
        return "legacy_conversion"

    # 3. Image-ink blocks only → OCR
    if is_ocr_eligible_block(meta, btype):
        return "ocr"

    # 4. Table
    if btype == "table" or contains_table:
        return "table_extraction"

    # 5. Stamp
    if btype == "stamp":
        return "placeholder_stamp"

    if btype == "image":
        return "placeholder_image"

    if contains_text:
        return "pdf_extraction"

    return "skipped"


def _all_blocks_image_based(blocks: list[PageRegion]) -> bool:
    """True when every block is image-ink OCR eligible."""
    if not blocks:
        return False
    for region in blocks:
        meta = _block_meta(region)
        btype = _block_type(region)
        if not is_ocr_eligible_block(meta, btype):
            return False
    return True


def _extract_whole_page_ocr(
    page: fitz.Page,
    lang: str,
    page_intel: PageIntelligence,
) -> dict[str, Any]:
    """Last resort: OCR entire page when every block is image-only."""
    from app.extract.precision_pipeline import extract_page_precision

    logger.info("Page %d: all blocks image-based — whole-page OCR", page_intel.page_number)
    result = extract_page_precision(page, lang)
    return {
        "text": result.get("text", ""),
        "confidence": result.get("confidence", 0.0),
        "method": "whole_page_ocr",
        "regions": [{
            "block_id": f"p{page_intel.page_number:03d}_full",
            "block_type": "image",
            "method": result.get("method", "image_ocr"),
            "confidence": result.get("confidence", 0.0),
            "route": "whole_page_ocr",
        }],
        "block_count": 1,
    }


def _attach_font_metadata(meta: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    """Copy per-block font profile from extraction result into router metadata."""
    for key in (
        "fonts", "font_confidence", "is_mixed_fonts", "encoding",
        "has_legacy_font", "has_unicode_font", "dominant_font",
        "converted_spans", "total_spans",
    ):
        if key in result:
            meta[key] = result[key]
    return meta


def _extract_unicode(
    page: fitz.Page,
    bbox: tuple[float, ...],
    font_lookup: dict[str, Any] | None,
    meta: dict[str, Any],
) -> ExtractionResult:
    from app.extract.direct_extract import extract_block_direct

    result = extract_block_direct(page, bbox, font_lookup)
    meta = _attach_font_metadata(meta, result)
    if result.get("text", "").strip():
        return ExtractionResult(
            result["text"],
            result.get("confidence", 90.0),
            "pdf_extraction",
            metadata={**meta, "sub_method": result.get("method")},
        )
    return ExtractionResult("", 0.0, "no_text", metadata=meta)


def _extract_legacy(
    page: fitz.Page,
    bbox: tuple[float, ...],
    font_lookup: dict[str, Any] | None,
    meta: dict[str, Any],
) -> ExtractionResult:
    from app.extract.direct_extract import extract_block_direct

    result = extract_block_direct(page, bbox, font_lookup)
    meta = _attach_font_metadata(meta, result)
    if result.get("text", "").strip():
        return ExtractionResult(
            result["text"],
            result.get("confidence", 85.0),
            "legacy_conversion",
            metadata={
                **meta,
                "legacy_fonts": result.get("legacy_fonts", []),
            },
        )
    return ExtractionResult("", 0.0, "no_text", metadata=meta)


def _extract_table(
    page: fitz.Page,
    bbox: tuple[float, ...],
    font_lookup: dict[str, Any] | None,
    meta: dict[str, Any],
) -> ExtractionResult:
    from app.extract.table_extractor import extract_tables_from_page

    try:
        for table in extract_tables_from_page(page, font_lookup):
            tbbox = table["bbox"]
            if _boxes_overlap(bbox, tbbox):
                return ExtractionResult(
                    table["formatted"],
                    85.0,
                    "table_extraction",
                    metadata={**meta, "rows": len(table.get("rows", []))},
                )
    except Exception as exc:
        logger.debug("Table extraction failed: %s", exc)

    return _extract_unicode(page, bbox, font_lookup, meta)


def _extract_ocr_block(
    page: fitz.Page,
    bbox: tuple[float, ...],
    lang: str,
    page_intel: PageIntelligence | None,
    meta: dict[str, Any],
) -> ExtractionResult:
    from app.extract.render import pixmap_to_bgr
    from app.ocr.preprocess import preprocess_for_ocr
    from app.ocr.engine import run_ocr_block

    btype = meta.get("block_type", "")
    if not is_ocr_eligible_block(meta, btype):
        logger.debug("Block %s not OCR-eligible — skipping", meta.get("block_id"))
        return ExtractionResult("", 0.0, "ocr_skipped", metadata=meta)

    try:
        clip = fitz.Rect(bbox)
        clip.x0 = max(0, clip.x0 - 4)
        clip.y0 = max(0, clip.y0 - 4)
        clip.x1 += 4
        clip.y1 += 4

        page_rotation = int(meta.get("rotation", 0))
        quality_tier = "moderate"
        if page_intel is not None:
            quality_tier = (
                "noisy" if page_intel.page_type.value == "scanned_noisy" else "moderate"
            )

        # Render at DPI based on expected quality
        dpi = {"clean": 300, "moderate": 350, "noisy": 400, "poor": 450}.get(
            quality_tier, 350,
        )
        mat = fitz.Matrix(dpi / 72.0, dpi / 72.0)
        pix = page.get_pixmap(matrix=mat, clip=clip, alpha=False)
        image_bgr = pixmap_to_bgr(pix)
        del pix

        # Pre-OCR analysis
        analysis = analyze_image_block(
            image_bgr,
            render_dpi=dpi,
            page_rotation=page_rotation,
        )
        profile = analysis["preprocess_profile"]

        processed = preprocess_for_ocr(image_bgr, profile=profile)
        ocr = run_ocr_block(processed, lang, block_meta=meta, fast=False)
        del image_bgr, processed
        gc.collect()

        ocr_conf = ocr.get("ocr_confidence", ocr.get("mean_confidence", 0.0))

        return ExtractionResult(
            ocr.get("text", ""),
            ocr_conf,
            "ocr",
            char_confidences=ocr.get("char_confidences", []),
            metadata={
                **meta,
                "lang": ocr.get("lang_used", lang),
                "ocr_confidence": ocr_conf,
                "content_script": ocr.get("content_script"),
                "image_quality": {
                    "tier": analysis["quality_tier"],
                    "score": analysis["quality_score"],
                    "skew_degrees": analysis["skew_degrees"],
                    "noise": analysis["noise"],
                    "bleed_through": analysis["bleed_through"],
                    "shadows": analysis["shadows"],
                    "resolution": analysis["resolution"],
                    "orientation": analysis["orientation"],
                },
                "word_count": ocr.get("word_count", 0),
            },
        )
    except Exception as exc:
        logger.debug("Block OCR failed: %s", exc)
        return ExtractionResult("", 0.0, "ocr_failed", metadata=meta)


def _render_block_gray(page: fitz.Page, bbox: tuple[float, ...], dpi: int = 200) -> np.ndarray:
    clip = fitz.Rect(bbox)
    mat = fitz.Matrix(dpi / 72.0, dpi / 72.0)
    pix = page.get_pixmap(matrix=mat, clip=clip, alpha=False, colorspace=fitz.csGRAY)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w)
    del pix
    return img


def _extract_qr(
    page: fitz.Page,
    bbox: tuple[float, ...],
    meta: dict[str, Any],
) -> ExtractionResult:
    try:
        gray = _render_block_gray(page, bbox)
        detector = cv2.QRCodeDetector()
        decoded, points, _ = detector.detectAndDecode(gray)
        if decoded:
            return ExtractionResult(
                decoded.strip(),
                95.0,
                "qr_decode",
                metadata={**meta, "decoded": True},
            )
        if points is not None:
            return ExtractionResult(
                "[QR Code]",
                80.0,
                "qr_decode",
                metadata={**meta, "decoded": False},
            )
    except Exception as exc:
        logger.debug("QR decode failed: %s", exc)
    return ExtractionResult("[QR Code]", 50.0, "qr_decode_failed", metadata=meta)


def _extract_barcode(
    page: fitz.Page,
    bbox: tuple[float, ...],
    meta: dict[str, Any],
) -> ExtractionResult:
    try:
        gray = _render_block_gray(page, bbox)
        # Optional pyzbar support
        try:
            from pyzbar.pyzbar import decode as zbar_decode  # type: ignore

            decoded_items = zbar_decode(gray)
            if decoded_items:
                value = decoded_items[0].data.decode("utf-8", errors="replace").strip()
                return ExtractionResult(
                    value,
                    90.0,
                    "barcode_decode",
                    metadata={**meta, "decoded": True},
                )
        except ImportError:
            pass

        detector = cv2.QRCodeDetector()
        decoded, _, _ = detector.detectAndDecode(gray)
        if decoded:
            return ExtractionResult(decoded.strip(), 75.0, "barcode_decode", metadata=meta)
    except Exception as exc:
        logger.debug("Barcode decode failed: %s", exc)

    return ExtractionResult(_PLACEHOLDER_BARCODE, 45.0, "barcode_decode", metadata=meta)


def _boxes_overlap(
    a: tuple[float, ...],
    b: tuple[float, ...],
) -> bool:
    return (
        a[0] < b[2] and a[2] > b[0]
        and a[1] < b[3] and a[3] > b[1]
    )
