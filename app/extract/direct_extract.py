"""
Direct PDF text extraction with font-aware conversion.

NO IMAGE OCR - reads the PDF text layer directly.
Converts legacy fonts (Preeti, Kantipur) to Unicode with npttf2utf.
Validates and fixes common conversion errors.

Target: 95-100% accuracy on digital PDFs.
"""
from __future__ import annotations

import re
from typing import Any

import fitz

from app.legacy_fonts.converter import convert_legacy_text
from app.legacy_fonts.mappings import is_legacy_font, get_npttf2utf_map_name
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


def _fix_common_errors(text: str) -> str:
    """Fix common conversion errors and normalize text."""
    if not text:
        return text
    
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


def extract_page_direct(page: fitz.Page) -> dict[str, Any]:
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
                
                # Convert if legacy font
                if is_legacy_font(font_name):
                    legacy_fonts.add(font_name)
                    converted_text = convert_legacy_text(raw_text, font_name)
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
    }


def extract_document_direct(
    document_bytes: bytes,
    filetype: str = "pdf",
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
            result = extract_page_direct(page)
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
