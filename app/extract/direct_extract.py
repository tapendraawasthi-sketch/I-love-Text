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

from app.legacy_fonts.converter import convert_legacy_text
from app.legacy_fonts.mappings import is_legacy_font, get_npttf2utf_map_name
from app.legacy_fonts.preeti_map import is_likely_preeti
from app.nlp.font_detector import analyse_document_fonts, guess_font_from_text
from app.logging_config import get_logger

logger = get_logger("DirectExtract")

_DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]")
_DEVANAGARI_DIGIT_RE = re.compile(r"[०-९]")
_GARBAGE_RE = re.compile(r"undefined|NaN|\[object", re.I)


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
    """Fix common conversion errors and normalize text."""
    if not text:
        return text

    text = _normalize_unicode(text)

    # Fix common Preeti conversion issues
    fixes = [
        # Double matras
        (r"ाा", "ा"),
        (r"िि", "ि"),
        (r"ीी", "ी"),
        (r"ुु", "ु"),
        (r"ूू", "ू"),
        (r"ेे", "े"),
        (r"ैै", "ै"),
        (r"ोो", "ो"),
        (r"ौौ", "ौ"),
        # Misplaced chandrabindu
        (r"(.)(ँ)([ा-ौ])", r"\1\3\2"),
        # Extra halant at word boundaries
        (r"्\s", " "),
        (r"्$", ""),
        # Common character confusions in Preeti
        (r"।।", "।"),
        # Matra before consonant → after consonant
        (r"ि([क-ह])", r"\1ि"),
        # Normalize spaces
        (r"[ \t]+", " "),
        (r"\n{3,}", "\n\n"),
    ]
    
    result = text
    for pattern, replacement in fixes:
        result = re.sub(pattern, replacement, result)
    
    return result.strip()


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
    if font_info and font_info.get("is_legacy"):
        return True
    if is_legacy_font(font_name):
        return True
    if is_likely_preeti(raw_text):
        return True
    guess = guess_font_from_text(raw_text)
    return guess["family"] not in ("unicode", "unknown") and guess["confidence"] >= 40


def _convert_span(raw_text: str, font_name: str, font_info: dict[str, Any] | None) -> str:
    if not _should_convert_span(raw_text, font_name, font_info):
        return raw_text

    if font_info and font_info.get("conversion_map"):
        return convert_legacy_text(raw_text, font_info["conversion_map"])

    if is_legacy_font(font_name):
        return convert_legacy_text(raw_text, font_name)

    guess = guess_font_from_text(raw_text)
    if guess["family"] not in ("unicode", "unknown"):
        return convert_legacy_text(raw_text, guess["family"])

    return convert_legacy_text(raw_text, "preeti")


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

    # Penalise leftover legacy ASCII encoding and garbage markers
    penalty = 0.0
    if _GARBAGE_RE.search(text):
        penalty += 40.0
    if ascii_ratio > 0.25 and deva_ratio < 0.35:
        penalty += 25.0

    # Reward clean Devanagari output
    score = deva_ratio * 100.0 - penalty
    if deva_ratio >= 0.45:
        quality = "excellent"
    elif deva_ratio >= 0.25:
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
    
    detected_fonts: set[str] = set()
    legacy_fonts: set[str] = set()
    blocks_output: list[str] = []
    total_chars = 0
    converted_chars = 0
    
    for block in page_dict.get("blocks", []):
        if block.get("type") != 0:  # Text block only
            continue
        
        block_lines: list[str] = []
        
        for line in block.get("lines", []):
            line_parts: list[tuple[str, float]] = []  # (text, x0)
            
            for span in line.get("spans", []):
                font_name = span.get("font", "")
                raw_text = span.get("text", "")
                bbox = span.get("bbox", (0, 0, 0, 0))
                x0 = bbox[0]
                
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
                
                line_parts.append((converted_text, x0))
            
            if not line_parts:
                continue
            
            # Sort by x position and join with appropriate spacing
            line_parts.sort(key=lambda x: x[1])
            
            line_text = ""
            for i, (text, x0) in enumerate(line_parts):
                if i == 0:
                    line_text = text
                else:
                    prev_x0 = line_parts[i - 1][1]
                    gap = x0 - prev_x0
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
    
    # Confidence based on validation
    if validation["quality"] == "good":
        confidence = 98.0 if has_legacy else 100.0
    elif validation["quality"] == "low_devanagari":
        confidence = 70.0
    else:
        confidence = 50.0
    
    method = "direct_legacy" if has_legacy else "direct_unicode"

    quality = score_text_quality(final_text)

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
    }


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
    final_text = "\n\n".join(page_texts)
    final_text = _fix_common_errors(final_text)

    confidences = [r.get("confidence", 0.0) for r in page_results if r.get("text")]
    mean_confidence = round(sum(confidences) / len(confidences), 1) if confidences else 0.0
    quality = score_text_quality(final_text)

    no_text_pages = sum(1 for r in page_results if r.get("method") == "no_text")

    return {
        "text": final_text or "[No text layer found in PDF.]",
        "font_analysis": font_analysis,
        "pages": len(page_results),
        "page_results": page_results,
        "confidence": mean_confidence,
        "quality": quality,
        "no_text_pages": no_text_pages,
        "method": "direct_font_conversion",
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
