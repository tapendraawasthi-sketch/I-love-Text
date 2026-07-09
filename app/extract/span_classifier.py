"""
Per-span font classification for mixed-font documents.

Every text span is independently classified and converted.
This prevents the common error of applying Preeti conversion
to Unicode text or vice versa.

A single line might contain:
    [Preeti span] [Unicode span] [English span]
Each is handled independently.
"""
from __future__ import annotations

import re
from typing import Any

from app.extract.document_model import (
    TextSpan, FontEncoding, BBox
)
from app.legacy_fonts.converter import (
    is_legacy_encoded, force_convert_legacy, is_plain_ascii_text
)
from app.legacy_fonts.mappings import is_legacy_font, get_npttf2utf_map_name
from app.legacy_fonts.preeti_map import is_likely_preeti
from app.nlp.font_detector import identify_font, guess_font_from_text
from app.logging_config import get_logger

logger = get_logger("SpanClassifier")

_DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]")
_DECORATIVE_FONTS = ("wingdings", "webdings", "symbol", "zapfdingbats")


def classify_span(
    raw_text: str,
    font_name: str,
    font_size: float = 0.0,
    is_bold: bool = False,
    is_italic: bool = False,
    color: int = 0,
    bbox: tuple = (0, 0, 0, 0),
    font_lookup: dict[str, Any] | None = None,
) -> TextSpan:
    """
    Classify a single text span and convert if necessary.
    
    This is the core per-span handler that ensures each span
    is independently and correctly processed.
    """
    span = TextSpan(
        text=raw_text,
        bbox=BBox.from_tuple(bbox),
        font_name=font_name,
        font_size=font_size,
        is_bold=is_bold,
        is_italic=is_italic,
        color=color,
    )

    if not raw_text or not raw_text.strip():
        span.font_encoding = FontEncoding.UNKNOWN
        span.converted_text = raw_text
        return span

    # 1. Check for decorative fonts (skip entirely)
    if any(d in font_name.lower() for d in _DECORATIVE_FONTS):
        span.font_encoding = FontEncoding.UNKNOWN
        span.converted_text = ""
        return span

    # 2. Check if already Unicode Devanagari
    deva_chars = sum(1 for c in raw_text if _DEVANAGARI_RE.match(c))
    total_chars = sum(1 for c in raw_text if c.strip())
    deva_ratio = deva_chars / max(total_chars, 1)

    if deva_ratio >= 0.5:
        # Already proper Unicode Devanagari
        span.font_encoding = FontEncoding.UNICODE
        span.converted_text = raw_text
        span.confidence = 100.0
        return span

    # 3. Check if plain ASCII (URLs, emails, pure English)
    if is_plain_ascii_text(raw_text):
        span.font_encoding = FontEncoding.UNICODE
        span.converted_text = raw_text
        span.confidence = 100.0
        return span

    # 4. Identify font from PDF metadata
    font_info = None
    if font_lookup and font_name in font_lookup:
        font_info = font_lookup[font_name]

    font_id = identify_font(font_name)
    family = font_id["family"]
    conversion_map = font_id.get("conversion_map")

    # 5. Determine encoding
    if family != "unicode" and family != "unknown":
        # Known legacy font family from PDF metadata
        span.font_encoding = _family_to_encoding(family)
        span.converted_text = _convert_with_best_map(
            raw_text, conversion_map or family
        )
        span.confidence = 90.0
        return span

    if font_info and font_info.get("is_legacy"):
        # Font analysis detected legacy
        map_name = font_info.get("conversion_map", "preeti")
        span.font_encoding = _family_to_encoding(map_name)
        span.converted_text = _convert_with_best_map(raw_text, map_name)
        span.confidence = 85.0
        return span

    # 6. Content-based detection (when font name is generic)
    if is_legacy_encoded(raw_text):
        # Text looks like legacy encoding
        guess = guess_font_from_text(raw_text)
        map_name = guess.get("family", "preeti")
        if map_name in ("unicode", "unknown"):
            map_name = "preeti"
        span.font_encoding = _family_to_encoding(map_name)
        span.converted_text = _convert_with_best_map(raw_text, map_name)
        span.confidence = float(guess.get("confidence", 50))
        return span

    # 7. Default: treat as Unicode passthrough
    span.font_encoding = FontEncoding.UNICODE
    span.converted_text = raw_text
    span.confidence = 80.0
    return span


def _family_to_encoding(family: str) -> FontEncoding:
    """Map font family string to FontEncoding enum."""
    mapping = {
        "preeti": FontEncoding.PREETI,
        "kantipur": FontEncoding.KANTIPUR,
        "sagarmatha": FontEncoding.SAGARMATHA,
        "himali": FontEncoding.HIMALI,
        "aakriti": FontEncoding.AAKRITI,
        "pcsnepali": FontEncoding.PCS_NEPALI,
        "unicode": FontEncoding.UNICODE,
    }
    return mapping.get(family, FontEncoding.UNKNOWN_LEGACY)


def _convert_with_best_map(text: str, primary_map: str) -> str:
    """
    Try primary map, then fallback maps. Return best quality result.
    """
    from app.extract.direct_extract import score_text_quality

    maps_to_try = [primary_map]
    if primary_map != "preeti":
        maps_to_try.append("preeti")
    if primary_map != "kantipur":
        maps_to_try.append("kantipur")

    best = text
    best_score = score_text_quality(text).get("score", 0)

    for map_name in maps_to_try:
        try:
            converted = force_convert_legacy(text, map_name)
            score = score_text_quality(converted).get("score", 0)
            if score > best_score:
                best = converted
                best_score = score
        except Exception:
            continue

    return best
