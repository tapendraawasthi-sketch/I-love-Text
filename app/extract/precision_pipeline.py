"""
Per-page precision extraction for maximum accuracy.

Strategy per page:
  1. Unicode text layer → exact digital text
  2. Legacy font layer (Preeti, Kantipur) → font-aware conversion (NOT OCR)
  3. Conversion quality gate fails / scanned → OCR
"""
from __future__ import annotations

import gc
import re
from typing import Any

import fitz

from app.config import SANITIZE_MAX_JPEG_BYTES
from app.extract.ocr_pipeline import ocr_image
from app.extract.page_raster import rasterize_page_for_ocr
from app.extract.raster_pipeline import sanitize_dpi_for_page_count
from app.legacy_fonts.mappings import is_legacy_font
from app.logging_config import get_logger
from app.ocr.engine import resolve_ocr_lang

logger = get_logger("PrecisionPipeline")

_DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]")
_LEGACY_FONT_RE = re.compile(
    r"preeti|kantipur|sagarmatha|himali|pcs|aakriti|fontasy|ganesh|navjeevan|kanchan|himalb|annapurna|sabdatara",
    re.I,
)
_GARBAGE_RE = re.compile(r"undefined|NaN|\[object|function\s*\(", re.I)


def _devanagari_ratio(text: str) -> float:
    letters = [c for c in text if c.strip()]
    if not letters:
        return 0.0
    return sum(1 for c in letters if _DEVANAGARI_RE.match(c)) / len(letters)


def _extract_digital_page(page: fitz.Page) -> dict[str, Any] | None:
    """
    Extract text from PDF text layer.

    Unicode fonts: passthrough.
    Legacy fonts: convert via direct_extract (font maps / npttf2utf).
    OCR is used only when conversion quality fails.
    """
    page_dict = page.get_text("dict")
    detected_fonts: set[str] = set()
    has_legacy_font = False

    for block in page_dict.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                font_name = span.get("font", "")
                raw = span.get("text", "")
                if not raw.strip():
                    continue
                detected_fonts.add(font_name)
                if is_legacy_font(font_name) or _LEGACY_FONT_RE.search(font_name):
                    has_legacy_font = True

    # Legacy digital PDFs: convert text layer — do NOT discard for OCR
    if has_legacy_font:
        try:
            from app.extract.direct_extract import extract_page_direct
            converted = extract_page_direct(page)
            text = (converted.get("text") or "").strip()
            quality = converted.get("quality") or {}
            conf = float(converted.get("confidence") or 0)
            if text and (
                conf >= 50
                or quality.get("score", 0) >= 40
                or _devanagari_ratio(text) >= 0.20
            ):
                logger.info(
                    "Legacy fonts %s converted via direct_extract (conf=%.1f)",
                    [f for f in detected_fonts if is_legacy_font(f) or _LEGACY_FONT_RE.search(f)],
                    conf,
                )
                return {
                    "text": text,
                    "method": converted.get("method", "direct_legacy"),
                    "confidence": conf if conf else 90.0,
                    "char_count": len(text),
                    "devanagari_ratio": round(_devanagari_ratio(text) * 100, 1),
                }
            logger.info(
                "Legacy conversion quality too low (conf=%.1f) — falling back to OCR",
                conf,
            )
            return None
        except Exception as exc:
            logger.warning("Legacy direct extraction failed: %s — OCR fallback", exc)
            return None

    # Unicode / unknown digital path
    blocks_out: list[str] = []
    for block in page_dict.get("blocks", []):
        if block.get("type") != 0:
            continue

        block_lines: list[str] = []
        for line in block.get("lines", []):
            spans_text: list[tuple[str, float, float, float]] = []
            for span in line.get("spans", []):
                raw = span.get("text", "")
                if not raw.strip():
                    continue
                bbox = span.get("bbox", (0, 0, 0, 0))
                spans_text.append((raw, bbox[0], bbox[2], bbox[3] - bbox[1]))

            if not spans_text:
                continue

            spans_text.sort(key=lambda x: x[1])
            line_str = ""
            for i, (text, x0, x1, height) in enumerate(spans_text):
                if i == 0:
                    line_str += text
                else:
                    prev_x1 = spans_text[i - 1][2]
                    gap = x0 - prev_x1
                    h = max(spans_text[i - 1][3], 8.0)
                    if gap > h * 1.4:
                        line_str += "\t" + text
                    elif gap > 2:
                        line_str += " " + text
                    else:
                        line_str += text
            block_lines.append(line_str)

        if block_lines:
            blocks_out.append("\n".join(block_lines))

    text = "\n\n".join(blocks_out).strip()
    if len(text.strip()) < 3:
        return None

    if _GARBAGE_RE.search(text):
        logger.info("Garbage patterns detected in text, using OCR")
        return None

    ratio = _devanagari_ratio(text)
    char_count = len(text.strip())

    if ratio >= 0.25:
        return {
            "text": text,
            "method": "digital_unicode",
            "confidence": 100.0,
            "char_count": char_count,
            "devanagari_ratio": round(ratio * 100, 1),
        }

    if ratio >= 0.08 or (char_count > 50 and re.search(r"[a-zA-Z]{3,}", text)):
        return {
            "text": text,
            "method": "digital_mixed",
            "confidence": 100.0,
            "char_count": char_count,
            "devanagari_ratio": round(ratio * 100, 1),
        }

    # Low Devanagari with no clear English — try legacy conversion before OCR
    if ratio < 0.05 and not re.search(r"[a-zA-Z]{5,}", text):
        try:
            from app.extract.direct_extract import extract_page_direct
            converted = extract_page_direct(page)
            ctext = (converted.get("text") or "").strip()
            if ctext and _devanagari_ratio(ctext) >= 0.20:
                return {
                    "text": ctext,
                    "method": converted.get("method", "direct_legacy"),
                    "confidence": float(converted.get("confidence") or 85.0),
                    "char_count": len(ctext),
                    "devanagari_ratio": round(_devanagari_ratio(ctext) * 100, 1),
                }
        except Exception:
            pass
        logger.info("Low Devanagari ratio (%.1f%%), using OCR", ratio * 100)
        return None

    return None


def extract_page_precision(
    page: fitz.Page,
    lang: str,
    *,
    render_dpi: int = 300,
    max_jpeg_bytes: int = SANITIZE_MAX_JPEG_BYTES,
) -> dict[str, Any]:
    """Extract one page with the most accurate available method."""
    digital = _extract_digital_page(page)
    if digital and digital["char_count"] >= 8:
        return digital

    # Higher DPI for Nepali OCR (Devanagari needs more detail)
    ocr_dpi = max(render_dpi, 350)

    image_bgr, raster_meta = rasterize_page_for_ocr(
        page,
        ocr_dpi,
        max_jpeg_bytes=max_jpeg_bytes,
    )
    resolved = resolve_ocr_lang(lang)
    ocr = ocr_image(image_bgr, resolved, digital=True, fast=False)
    del image_bgr
    gc.collect()
    return {
        "text": ocr["text"],
        "method": "image_ocr",
        "confidence": ocr["mean_confidence"],
        "char_count": len(ocr["text"].strip()),
        "lang_used": ocr.get("lang_used", lang),
        "raster": raster_meta,
        "devanagari_ratio": round(_devanagari_ratio(ocr["text"]) * 100, 1),
    }


def extract_document_precision(
    document_bytes: bytes,
    filetype: str,
    lang: str,
    *,
    render_dpi: int | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Precision extraction for every page in a PDF or DOCX."""
    try:
        doc = fitz.open(stream=document_bytes, filetype=filetype)
    except Exception as exc:
        raise ValueError(f"Failed to open {filetype.upper()} document: {exc}") from exc

    page_count = len(doc)
    dpi = render_dpi or sanitize_dpi_for_page_count(page_count)
    size_mb = len(document_bytes) / (1024 * 1024)
    max_jpeg = 650_000 if size_mb > 70 else SANITIZE_MAX_JPEG_BYTES

    results: list[dict[str, Any]] = []
    methods: list[str] = []

    try:
        for index in range(page_count):
            page = doc.load_page(index)
            result = extract_page_precision(
                page,
                lang,
                render_dpi=dpi,
                max_jpeg_bytes=max_jpeg,
            )
            results.append(result)
            methods.append(result["method"])
            if page_count >= 20 and index % 20 == 0:
                logger.info("Page %s/%s: %s", index + 1, page_count, result["method"])
            elif page_count < 20:
                logger.info("Page %s/%s: %s", index + 1, page_count, result["method"])
            gc.collect()
    finally:
        doc.close()

    digital_count = sum(
        1 for m in methods
        if m.startswith("digital") or m.startswith("direct")
    )
    ocr_count = len(methods) - digital_count

    meta = {
        "pipeline": "precision_hybrid",
        "digital_pages": digital_count,
        "ocr_pages": ocr_count,
        "method_per_page": methods,
        "file_size_mb": round(size_mb, 2),
        "ocr_dpi": dpi,
        "compressed_ocr_pages": ocr_count > 0,
    }
    return results, meta
