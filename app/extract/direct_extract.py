"""
Direct PDF text extraction with font-aware conversion.

NO IMAGE OCR - reads the PDF text layer directly.
Converts legacy fonts (Preeti, Kantipur) to Unicode with npttf2utf.
Validates and fixes common conversion errors.

For PDFs with an embedded text layer this is more accurate than OCR because
characters are read from the document, not guessed from pixels.

Target: 95-100% accuracy on digital PDFs.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

import fitz

from app.legacy_fonts.converter import (
    is_legacy_encoded,
    force_convert_legacy,
    is_plain_ascii_text,
    is_legacy_font,
)
from app.legacy_fonts.mappings import get_npttf2utf_map_name
from app.legacy_fonts.preeti_map import is_likely_preeti
from app.nlp.font_detector import analyse_document_fonts, guess_font_from_text
from app.extract.unicode_validator import repair_devanagari_unicode, validate_devanagari_text
from app.logging_config import get_logger

logger = get_logger("DirectExtract")

_DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]")
_DEVANAGARI_DIGIT_RE = re.compile(r"[०-९]")
_GARBAGE_RE = re.compile(r"undefined|NaN|\[object", re.I)
_GARBAGE_SYMBOL_RE = re.compile(r"[|=\[\]{}<>^~`\\]")
_LONG_DEVANAGARI_RUN_RE = re.compile(r"[\u0900-\u097F]{22,}")
_PUA_RE = re.compile(r"[\uE000-\uF8FF]")
_DECORATIVE_FONT_FRAGMENTS = ("wingdings", "webdings", "symbol", "zapfdingbats")


def _is_decorative_font(font_name: str) -> bool:
    fl = font_name.lower()
    return any(d in fl for d in _DECORATIVE_FONT_FRAGMENTS)


def _is_preeti_font(font_name: str) -> bool:
    return "preeti" in font_name.lower()


def _devanagari_ratio(text: str) -> float:
    """Calculate ratio of Devanagari characters in text."""
    chars = [c for c in text if c.strip()]
    if not chars:
        return 0.0
    return sum(1 for c in chars if _DEVANAGARI_RE.match(c)) / len(chars)


def _normalize_unicode(text: str) -> str:
    if not text:
        return text
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text


def _fix_common_errors(text: str) -> str:
    """Fix common conversion errors and normalize text with Unicode validation."""
    if not text:
        return text

    text = _normalize_unicode(text)

    # Apply Devanagari-specific Unicode repairs (matra reordering, etc.)
    if _DEVANAGARI_RE.search(text):
        text = repair_devanagari_unicode(text)

    # Remove PUA characters
    text = _PUA_RE.sub("", text)

    # Normalize spaces
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


def _validate_conversion(original: str, converted: str, font_name: str) -> dict[str, Any]:
    """
    Validate conversion quality and return diagnostics.
    """
    orig_len = len(original.strip())
    conv_len = len(converted.strip())
    deva_ratio = _devanagari_ratio(converted)
    
    # Check for garbage patterns
    has_garbage = bool(_GARBAGE_RE.search(converted))
    
    # Length should be roughly similar (within 50%)
    length_ok = orig_len == 0 or (0.5 <= conv_len / max(orig_len, 1) <= 2.0)
    
    # For legacy fonts, expect high Devanagari ratio after conversion
    is_legacy = is_legacy_font(font_name)
    deva_ok = not is_legacy or deva_ratio >= 0.3 or orig_len < 10
    
    quality = "good"
    if has_garbage:
        quality = "garbage"
    elif not length_ok:
        quality = "length_mismatch"
    elif is_legacy and deva_ratio < 0.15 and orig_len > 20:
        quality = "low_devanagari"
    
    return {
        "quality": quality,
        "devanagari_ratio": round(deva_ratio * 100, 1),
        "original_length": orig_len,
        "converted_length": conv_len,
        "is_legacy_font": is_legacy,
        "font_name": font_name,
    }


def build_font_lookup(font_analysis: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Map raw PDF font names to conversion strategy from font analysis."""
    lookup: dict[str, dict[str, Any]] = {}
    for font in font_analysis.get("fonts_found", []):
        lookup[font["raw_name"]] = font
    return lookup


def _should_convert_span(raw_text: str, font_name: str, font_info: dict[str, Any] | None) -> bool:
    if not raw_text or not raw_text.strip():
        return False
    if _is_decorative_font(font_name):
        return False
    if is_plain_ascii_text(raw_text):
        return False

    # PDF font name is ground truth for Preeti/Kantipur spans.
    if _is_preeti_font(font_name) or is_legacy_font(font_name):
        return True
    if font_info and font_info.get("is_legacy"):
        return True
    return is_legacy_encoded(raw_text)


def _convert_span(raw_text: str, font_name: str, font_info: dict[str, Any] | None) -> str:
    if _is_decorative_font(font_name):
        return ""
    if not _should_convert_span(raw_text, font_name, font_info):
        return raw_text

    primary_map = None
    if font_info and font_info.get("conversion_map"):
        primary_map = font_info["conversion_map"]
    elif _is_preeti_font(font_name):
        primary_map = "preeti"
    elif is_legacy_font(font_name):
        primary_map = get_npttf2utf_map_name(font_name)
    else:
        guess = guess_font_from_text(raw_text)
        if guess["family"] not in ("unicode", "unknown"):
            primary_map = guess["family"]

    maps_to_try: list[str] = []
    for m in (primary_map, "preeti", "kantipur"):
        if m and m not in maps_to_try:
            maps_to_try.append(m)

    best = raw_text
    best_score = score_text_quality(raw_text)["score"]
    for map_name in maps_to_try:
        converted = force_convert_legacy(raw_text, map_name)
        converted_score = score_text_quality(converted)["score"]
        if converted_score > best_score:
            best = converted
            best_score = converted_score

    return best


def score_text_quality(text: str) -> dict[str, Any]:
    """Score converted text — higher is better. Used to pick best output path."""
    if not text or not text.strip():
        return {"score": 0.0, "devanagari_ratio": 0.0, "quality": "empty"}

    chars = [c for c in text if c.strip()]
    if not chars:
        return {"score": 0.0, "devanagari_ratio": 0.0, "quality": "empty"}

    deva_ratio = sum(1 for c in chars if _DEVANAGARI_RE.match(c)) / len(chars)
    ascii_letters = sum(1 for c in chars if c.isascii() and c.isalpha())
    ascii_ratio = ascii_letters / len(chars)

    penalty = 0.0
    if _GARBAGE_RE.search(text):
        penalty += 40.0

    symbol_hits = len(_GARBAGE_SYMBOL_RE.findall(text))
    penalty += min(30.0, symbol_hits * 2.0)

    long_runs = len(_LONG_DEVANAGARI_RUN_RE.findall(text))
    penalty += long_runs * 8.0

    words = _DEVANAGARI_RE.findall(text)
    if len(words) >= 5:
        counts = {}
        for w in words:
            counts[w] = counts.get(w, 0) + 1
        dominant = max(counts.values()) / len(words)
        if dominant > 0.18:
            penalty += 25.0

    if ascii_ratio > 0.20 and deva_ratio < 0.40:
        penalty += 25.0

    score = deva_ratio * 100.0 - penalty
    if deva_ratio >= 0.45 and penalty < 10:
        quality = "excellent"
    elif deva_ratio >= 0.25 and penalty < 20:
        quality = "good"
    elif deva_ratio >= 0.10:
        quality = "partial"
    else:
        quality = "poor"

    return {
        "score": round(max(0.0, score), 1),
        "devanagari_ratio": round(deva_ratio * 100, 1),
        "ascii_ratio": round(ascii_ratio * 100, 1),
        "quality": quality,
        "penalty": round(penalty, 1),
    }


def extract_page_direct(page: fitz.Page, font_lookup: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """
    Extract text directly from PDF page with font-aware conversion.
    
    Returns:
        {
            "text": str,
            "method": "direct_unicode" | "direct_legacy" | "no_text",
            "confidence": float,
            "fonts": list[str],
            "validation": dict,
        }
    """
    page_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)

    # --- Detect table bounding boxes so we can skip them in block extraction ---
    from app.extract.table_extractor import extract_tables_from_page, get_table_bboxes
    table_bboxes = get_table_bboxes(page)
    tables = extract_tables_from_page(page, font_lookup)

    def _in_table(bbox: tuple) -> bool:
        """Return True if bbox overlaps any detected table region."""
        x0, y0, x1, y1 = bbox[:4]
        for tx0, ty0, tx1, ty1 in table_bboxes:
            overlap_x = x0 < tx1 and x1 > tx0
            overlap_y = y0 < ty1 and y1 > ty0
            if overlap_x and overlap_y:
                return True
        return False
    
    detected_fonts: set[str] = set()
    legacy_fonts: set[str] = set()
    blocks_output: list[str] = []
    total_chars = 0
    converted_chars = 0
    
    for block in page_dict.get("blocks", []):
        # Skip blocks that are inside a detected table (handled separately)
        block_bbox = block.get("bbox", (0, 0, 0, 0))
        if table_bboxes and _in_table(block_bbox):
            continue

        if block.get("type") != 0:  # Text block only
            continue
        
        block_lines: list[str] = []
        
        for line in block.get("lines", []):
            line_parts: list[tuple[str, float, float]] = []  # (text, x0, x1)

            for span in line.get("spans", []):
                font_name = span.get("font", "")
                raw_text = span.get("text", "")
                bbox = span.get("bbox", (0, 0, 0, 0))
                x0 = bbox[0]
                x1 = bbox[2]
                
                if not raw_text:
                    continue
                
                detected_fonts.add(font_name)
                total_chars += len(raw_text)

                font_info = (font_lookup or {}).get(font_name)
                if _should_convert_span(raw_text, font_name, font_info):
                    legacy_fonts.add(font_name)
                    converted_text = _convert_span(raw_text, font_name, font_info)
                    converted_chars += len(raw_text)
                else:
                    converted_text = raw_text
                
                line_parts.append((converted_text, x0, x1))
            
            if not line_parts:
                continue
            
            # Sort by x position and join with appropriate spacing
            line_parts.sort(key=lambda x: x[1])
            
            line_text = ""
            for i, (text, x0, x1) in enumerate(line_parts):
                if i == 0:
                    line_text = text
                else:
                    prev_x1 = line_parts[i - 1][2]
                    # True visual gap: start of this span to END of the
                    # previous span. Using x0-to-x0 (start-to-start)
                    # instead of x0-to-x1 (end-to-start) was a real bug:
                    # it included the width of the previous span, so any
                    # multi-character span wider than the ~5pt threshold
                    # (e.g. a two-character consonant+matra run such as
                    # "कु") caused a false space to be inserted before the
                    # next span even when the two spans were visually
                    # touching -- producing exactly the "कु ल" (should be
                    # "कुल") mid-word space corruption seen in real
                    # extracted documents.
                    gap = x0 - prev_x1
                    # Large gap = tab (table column), small gap = space
                    if gap > 50:
                        line_text += "\t" + text
                    elif gap > 5:
                        line_text += " " + text
                    else:
                        line_text += text
            
            block_lines.append(line_text)
        
        if block_lines:
            blocks_output.append("\n".join(block_lines))
    
    # Combine blocks
    raw_output = "\n\n".join(blocks_output)

    # --- Merge table content at correct positions in output ---
    if tables:
        # Build a list of (y0, text) for both normal blocks and tables
        block_items: list[tuple[float, str]] = []
        for blk, blk_text in zip(page_dict.get("blocks", []), blocks_output):
            block_items.append((blk.get("bbox", (0, 0, 0, 0))[1], blk_text))
        for tbl in tables:
            block_items.append((tbl["bbox"][1], "\n" + tbl["formatted"] + "\n"))
        block_items.sort(key=lambda x: x[0])
        raw_output = "\n\n".join(text for _, text in block_items)
    
    # Apply fixes
    final_text = _fix_common_errors(raw_output)
    
    # Determine method
    if not final_text.strip():
        return {
            "text": "",
            "method": "no_text",
            "confidence": 0.0,
            "char_count": 0,
            "fonts": list(detected_fonts),
            "legacy_fonts": list(legacy_fonts),
        }
    
    has_legacy = bool(legacy_fonts)
    deva_ratio = _devanagari_ratio(final_text)
    
    # Validate
    validation = _validate_conversion(
        raw_output, 
        final_text,
        list(legacy_fonts)[0] if legacy_fonts else ""
    )
    
    quality = score_text_quality(final_text)

    # Confidence reflects actual output quality, not just font detection.
    if quality["score"] >= 75:
        confidence = min(100.0, quality["score"])
    elif quality["score"] >= 50:
        confidence = quality["score"]
    elif validation["quality"] == "good":
        confidence = 70.0
    else:
        confidence = max(30.0, quality["score"])

    method = "direct_legacy" if has_legacy else "direct_unicode"

    return {
        "text": final_text,
        "method": method,
        "confidence": confidence,
        "char_count": len(final_text.strip()),
        "devanagari_ratio": round(deva_ratio * 100, 1),
        "fonts": list(detected_fonts),
        "legacy_fonts": list(legacy_fonts),
        "converted_ratio": round(converted_chars / max(total_chars, 1) * 100, 1),
        "validation": validation,
        "quality": quality,
        "tables": tables,
    }


def _extract_page_raw(page: fitz.Page) -> str:
    """Plain text extraction with no font conversion."""
    return page.get_text("text", flags=fitz.TEXT_PRESERVE_WHITESPACE).strip()


def extract_document_high_accuracy(pdf_bytes: bytes) -> dict[str, Any]:
    """
    Highest-accuracy path for PDFs with embedded text layers.

    1. Analyse every font in the document
    2. Extract text spans and convert each with the correct encoding map
    3. Apply rule-based Unicode cleanup (no LLM, no OCR)
  """
    font_analysis = analyse_document_fonts(pdf_bytes)
    font_lookup = build_font_lookup(font_analysis)

    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        raise ValueError(f"Cannot open PDF: {exc}") from exc

    page_results: list[dict[str, Any]] = []
    try:
        for idx in range(len(doc)):
            page = doc.load_page(idx)
            page_results.append(extract_page_direct(page, font_lookup))
    finally:
        doc.close()

    page_texts = [r["text"] for r in page_results if r.get("text", "").strip()]
    converted_text = _fix_common_errors("\n\n".join(page_texts))

    # For Preeti PDFs, converted output should always win over raw ASCII passthrough.
    final_text = converted_text
    method = "direct_font_conversion"
    if not font_analysis.get("dominant_family") == "preeti":
        try:
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            raw_pages = [_extract_page_raw(doc.load_page(i)) for i in range(len(doc))]
            doc.close()
            raw_text = _fix_common_errors("\n\n".join(p for p in raw_pages if p.strip()))
            if score_text_quality(raw_text)["score"] > score_text_quality(converted_text)["score"] + 3.0:
                final_text = raw_text
                method = "unicode_passthrough"
        except Exception:
            pass

    confidences = [r.get("confidence", 0.0) for r in page_results if r.get("text")]
    mean_confidence = round(sum(confidences) / len(confidences), 1) if confidences else 0.0
    quality = score_text_quality(final_text)

    no_text_pages = sum(1 for r in page_results if r.get("method") == "no_text")

    all_tables = [tbl for r in page_results for tbl in r.get("tables", [])]
    tables_by_method: dict[str, int] = {}
    for tbl in all_tables:
        method_name = tbl.get("detected_by", "unknown")
        tables_by_method[method_name] = tables_by_method.get(method_name, 0) + 1

    return {
        "text": final_text or "[No text layer found in PDF.]",
        "font_analysis": font_analysis,
        "pages": len(page_results),
        "page_results": page_results,
        "confidence": mean_confidence,
        "quality": quality,
        "no_text_pages": no_text_pages,
        "method": method,
        "tables_detected": len(all_tables),
        "tables_by_method": tables_by_method,
    }


def extract_document_direct(
    document_bytes: bytes,
    filetype: str = "pdf",
    font_lookup: dict[str, dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Extract text from entire document using direct text layer extraction.
    
    Falls back to OCR only if page has NO text layer at all.
    """
    try:
        doc = fitz.open(stream=document_bytes, filetype=filetype)
    except Exception as exc:
        raise ValueError(f"Failed to open document: {exc}") from exc
    
    page_count = len(doc)
    results: list[dict[str, Any]] = []
    all_fonts: set[str] = set()
    all_legacy_fonts: set[str] = set()
    methods: list[str] = []
    pages_needing_ocr: list[int] = []
    
    try:
        for idx in range(page_count):
            page = doc.load_page(idx)
            result = extract_page_direct(page, font_lookup)
            results.append(result)
            methods.append(result["method"])
            all_fonts.update(result.get("fonts", []))
            all_legacy_fonts.update(result.get("legacy_fonts", []))
            
            if result["method"] == "no_text":
                pages_needing_ocr.append(idx + 1)
            
            if page_count < 20 or idx % 20 == 0:
                logger.info(
                    "Page %d/%d: %s (%.1f%% Devanagari, fonts: %s)",
                    idx + 1, page_count,
                    result["method"],
                    result.get("devanagari_ratio", 0),
                    result.get("legacy_fonts", []) or result.get("fonts", [])[:2]
                )
    finally:
        doc.close()
    
    # Summary
    direct_unicode = sum(1 for m in methods if m == "direct_unicode")
    direct_legacy = sum(1 for m in methods if m == "direct_legacy")
    no_text = sum(1 for m in methods if m == "no_text")
    
    meta = {
        "pipeline": "direct_extraction",
        "total_pages": page_count,
        "direct_unicode_pages": direct_unicode,
        "direct_legacy_pages": direct_legacy,
        "no_text_pages": no_text,
        "pages_needing_ocr": pages_needing_ocr,
        "all_fonts": sorted(all_fonts),
        "legacy_fonts_found": sorted(all_legacy_fonts),
        "method_per_page": methods,
    }
    
    return results, meta
