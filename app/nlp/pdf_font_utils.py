"""
PDF font utilities — subset/CID parsing, per-block detection, confidence.

Handles embedded subset fonts (``ABCDEF+FontName``), CID/Type0 fonts, and
mixed-font blocks where each span is classified independently.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any

import fitz

from app.nlp.font_detector import identify_font, guess_font_from_text
from app.nlp.pdf_font_parse import parse_pdf_font_name

_DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]")


def resolve_font_info(
    raw_name: str,
    font_lookup: dict[str, dict[str, Any]] | None = None,
    sample_text: str = "",
) -> dict[str, Any]:
    """Resolve font metadata using lookup aliases and registry."""
    parsed = parse_pdf_font_name(raw_name)
    info: dict[str, Any] | None = None

    if font_lookup:
        info = (
            font_lookup.get(raw_name)
            or font_lookup.get(parsed["base_name"])
            or font_lookup.get(parsed["normalized_name"])
        )

    if info:
        merged = {**info, **parsed}
    else:
        identified = identify_font(parsed["base_name"])
        merged = {**identified, **parsed}
        merged["raw_name"] = raw_name

    if merged.get("family") == "unknown" and sample_text:
        guess = guess_font_from_text(sample_text)
        # Only adopt guess when confidence is high enough to avoid wrong-map conversion
        if guess["family"] not in ("unicode", "unknown") and guess.get("confidence", 0) >= 55:
            merged["family"] = guess["family"]
            merged["confidence"] = guess["confidence"]
            merged["is_legacy"] = True
            if not merged.get("conversion_map"):
                merged["conversion_map"] = guess["family"]
        elif guess["family"] == "unicode":
            merged["family"] = "unicode"
            merged["confidence"] = guess["confidence"]
            merged["is_legacy"] = False

    if "confidence" not in merged:
        merged["confidence"] = 90 if merged.get("family") != "unknown" else 40

    return merged


def is_unicode_text(text: str, threshold: float = 0.15) -> bool:
    """True when span text is already Unicode Devanagari (never convert)."""
    if not text or not text.strip():
        return False
    chars = [c for c in text if c.strip()]
    if not chars:
        return False
    deva = sum(1 for c in chars if _DEVANAGARI_RE.match(c)) / len(chars)
    return deva >= threshold


def is_unicode_font(font_info: dict[str, Any]) -> bool:
    """True when font should pass through without legacy conversion."""
    if font_info.get("family") == "unicode":
        return True
    if font_info.get("is_cid") and not font_info.get("is_legacy"):
        return True
    if font_info.get("encoding") == "unicode":
        return True
    return False


def should_convert_span(
    raw_text: str,
    font_info: dict[str, Any],
    *,
    is_decorative: bool = False,
    is_plain_ascii: bool = False,
    is_legacy_encoded_fn=None,
    is_legacy_font_fn=None,
) -> bool:
    """
    Decide whether a span needs legacy→Unicode conversion.

    Unicode text and Unicode/CID fonts are never converted.
    """
    if not raw_text or not raw_text.strip():
        return False
    if is_decorative:
        return False
    if is_plain_ascii:
        return False

    # Never convert Unicode Devanagari spans
    if is_unicode_text(raw_text):
        return False
    if is_unicode_font(font_info):
        return False

    raw_name = font_info.get("raw_name") or font_info.get("base_name") or ""
    if is_legacy_font_fn and is_legacy_font_fn(raw_name):
        return True
    if font_info.get("is_legacy"):
        return True
    if is_legacy_encoded_fn and is_legacy_encoded_fn(raw_text):
        return True
    return False


def analyse_span_font(
    span: dict[str, Any],
    font_lookup: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Classify a single PDF text span."""
    raw_name = span.get("font", "") or "UnknownFont"
    text = span.get("text", "") or ""
    info = resolve_font_info(raw_name, font_lookup, sample_text=text)

    encoding = "unicode" if is_unicode_font(info) or is_unicode_text(text) else (
        "legacy" if info.get("is_legacy") else "unknown"
    )

    return {
        "raw_name": raw_name,
        "base_name": info.get("base_name", raw_name),
        "family": info.get("family", "unknown"),
        "encoding": encoding,
        "is_legacy": bool(info.get("is_legacy")),
        "is_subset": bool(info.get("is_subset")),
        "is_cid": bool(info.get("is_cid")),
        "subset_id": info.get("subset_id"),
        "conversion_map": info.get("conversion_map"),
        "confidence": float(info.get("confidence", 70)),
        "char_count": len(text),
    }


def analyse_block_fonts(
    page: fitz.Page,
    bbox: tuple[float, ...],
    font_lookup: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Detect all fonts used inside a block bbox.

    Returns dominant font, mixed-font flag, encoding summary, and confidence.
    """
    clip = fitz.Rect(bbox)
    page_dict = page.get_text("dict", clip=clip, flags=fitz.TEXT_PRESERVE_WHITESPACE)

    font_chars: Counter[str] = Counter()
    font_span_samples: dict[str, str] = {}

    for block in page_dict.get("blocks", []):
        if block.get("type") != 0:
            continue
        bb = tuple(block.get("bbox", bbox))
        if not _bbox_overlaps(bb, bbox):
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = span.get("text", "") or ""
                if not text.strip():
                    continue
                fn = span.get("font", "") or "UnknownFont"
                font_chars[fn] += len(text)
                if fn not in font_span_samples:
                    font_span_samples[fn] = text[:80]

    if not font_chars:
        return {
            "fonts": [],
            "dominant_font": None,
            "font_confidence": 0.0,
            "is_mixed_fonts": False,
            "encoding": "unknown",
            "has_legacy_font": False,
            "has_unicode_font": False,
        }

    fonts: list[dict[str, Any]] = []
    family_chars: Counter[str] = Counter()
    encoding_chars: Counter[str] = Counter()
    weighted_conf = 0.0
    total_chars = 0

    for raw_name, char_count in font_chars.most_common():
        info = resolve_font_info(
            raw_name, font_lookup, sample_text=font_span_samples.get(raw_name, ""),
        )
        encoding = "unicode" if is_unicode_font(info) else (
            "legacy" if info.get("is_legacy") else "unknown"
        )
        entry = {
            "raw_name": raw_name,
            "base_name": info.get("base_name", raw_name),
            "matched_name": info.get("matched_name", raw_name),
            "family": info.get("family", "unknown"),
            "encoding": encoding,
            "is_legacy": bool(info.get("is_legacy")),
            "is_subset": bool(info.get("is_subset")),
            "is_cid": bool(info.get("is_cid")),
            "subset_id": info.get("subset_id"),
            "char_count": char_count,
            "share_percent": 0.0,
            "confidence": round(float(info.get("confidence", 70)), 1),
        }
        fonts.append(entry)
        family_chars[info.get("family", "unknown")] += char_count
        encoding_chars[encoding] += char_count
        weighted_conf += entry["confidence"] * char_count
        total_chars += char_count

    for entry in fonts:
        entry["share_percent"] = round(
            entry["char_count"] / max(total_chars, 1) * 100, 1,
        )

    dominant_font = fonts[0]["raw_name"] if fonts else None
    font_confidence = round(weighted_conf / max(total_chars, 1), 1)

    legacy_families = {
        f for f in family_chars if f not in ("unicode", "unknown")
    }
    has_legacy = encoding_chars.get("legacy", 0) > 0
    has_unicode = encoding_chars.get("unicode", 0) > 0
    is_mixed = (
        len(legacy_families) > 1
        or (has_legacy and has_unicode)
        or len(fonts) > 1 and len({f["family"] for f in fonts}) > 1
    )

    if has_legacy and has_unicode:
        encoding = "mixed"
    elif has_legacy:
        encoding = "legacy"
    elif has_unicode:
        encoding = "unicode"
    else:
        encoding = "unknown"

    return {
        "fonts": fonts,
        "dominant_font": dominant_font,
        "font_confidence": font_confidence,
        "is_mixed_fonts": is_mixed,
        "encoding": encoding,
        "has_legacy_font": has_legacy,
        "has_unicode_font": has_unicode,
    }


def enrich_font_lookup(font_analysis: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Build lookup with subset/CID aliases for span resolution."""
    lookup: dict[str, dict[str, Any]] = {}
    for font in font_analysis.get("fonts_found", []):
        raw = font.get("raw_name", "")
        if not raw:
            continue
        parsed = parse_pdf_font_name(raw)
        enriched = {**font, **parsed}
        lookup[raw] = enriched
        lookup[parsed["base_name"]] = enriched
        lookup[parsed["normalized_name"]] = enriched
    return lookup


def _bbox_overlaps(
    a: tuple[float, ...],
    b: tuple[float, ...],
    min_ratio: float = 0.15,
) -> bool:
    x0 = max(a[0], b[0])
    y0 = max(a[1], b[1])
    x1 = min(a[2], b[2])
    y1 = min(a[3], b[3])
    if x1 <= x0 or y1 <= y0:
        return False
    inter = (x1 - x0) * (y1 - y0)
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    return inter / max(area_a, 1.0) >= min_ratio
