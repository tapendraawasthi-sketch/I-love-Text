"""
Character-level glyph extraction from PDF pages.

Uses PyMuPDF's rawdict extraction to get individual character objects with
their exact positions, font information, and glyph IDs. This is the
foundation for accurate Unicode validation and repair.

Key insight: We extract at CHARACTER level (rawdict), not SPAN level (dict).
This gives us per-character bounding boxes and origins, which are essential
for detecting matra displacement errors.
"""
from __future__ import annotations

import re
from typing import Any

import fitz

from app.extract.glyph_model import (
    GlyphObject, GlyphConfidence, WordObject, ParagraphObject,
    classify_devanagari_char, is_valid_devanagari_sequence,
)
from app.extract.unicode_validator import (
    validate_devanagari_text, repair_devanagari_unicode,
    validate_and_repair_word,
)
from app.legacy_fonts.converter import is_legacy_encoded, is_plain_ascii_text
from app.legacy_fonts.mappings import is_legacy_font
from app.logging_config import get_logger

logger = get_logger("GlyphExtractor")

_DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]")
_DECORATIVE_FONTS = ("wingdings", "webdings", "symbol", "zapfdingbats")


def extract_glyphs_from_page(
    page: fitz.Page,
    font_lookup: dict[str, Any] | None = None,
) -> list[GlyphObject]:
    """
    Extract every character from a page as a GlyphObject.

    Uses rawdict extraction for character-level precision.
    Each character has its own bounding box, origin point, and font info.
    """
    glyphs: list[GlyphObject] = []

    try:
        page_dict = page.get_text(
            "rawdict",
            flags=(
                fitz.TEXT_PRESERVE_WHITESPACE |
                fitz.TEXT_PRESERVE_LIGATURES |
                fitz.TEXT_PRESERVE_IMAGES
            ),
        )
    except Exception as e:
        logger.warning("rawdict extraction failed, falling back to dict: %s", e)
        return _extract_glyphs_from_dict(page, font_lookup)

    for block in page_dict.get("blocks", []):
        if block.get("type") != 0:  # Text block only
            continue

        for line in block.get("lines", []):
            for span in line.get("spans", []):
                font_name = span.get("font", "")
                font_size = span.get("size", 0.0)
                flags = span.get("flags", 0)
                color = span.get("color", 0)
                is_bold = bool(flags & (1 << 4))
                is_italic = bool(flags & (1 << 1))

                # Skip decorative fonts
                if any(d in font_name.lower() for d in _DECORATIVE_FONTS):
                    continue

                chars = span.get("chars", [])
                for char_dict in chars:
                    unicode_char = char_dict.get("c", "")
                    if not unicode_char:
                        continue

                    bbox = char_dict.get("bbox", (0, 0, 0, 0))
                    origin = char_dict.get("origin", (0, 0))

                    props = classify_devanagari_char(unicode_char)

                    glyph = GlyphObject(
                        unicode_char=unicode_char,
                        raw_char=unicode_char,
                        x0=bbox[0],
                        y0=bbox[1],
                        x1=bbox[2],
                        y1=bbox[3],
                        origin_x=origin[0],
                        origin_y=origin[1],
                        font_name=font_name,
                        font_size=font_size,
                        is_bold=is_bold,
                        is_italic=is_italic,
                        color=color,
                        extraction_source="pymupdf_rawdict",
                        is_devanagari=props.get("is_devanagari", False),
                        is_matra=props.get("is_matra", False),
                        is_consonant=props.get("is_consonant", False),
                        is_vowel=props.get("is_vowel", False),
                        is_halant=props.get("is_halant", False),
                    )

                    # Determine initial confidence
                    if is_legacy_font(font_name):
                        glyph.confidence = GlyphConfidence.SUSPICIOUS
                        glyph.unicode_confidence = 30.0
                    elif glyph.is_devanagari:
                        glyph.confidence = GlyphConfidence.TRUSTED
                        glyph.unicode_confidence = 90.0
                    else:
                        glyph.confidence = GlyphConfidence.TRUSTED
                        glyph.unicode_confidence = 85.0

                    glyphs.append(glyph)

    return glyphs


def _extract_glyphs_from_dict(
    page: fitz.Page,
    font_lookup: dict[str, Any] | None = None,
) -> list[GlyphObject]:
    """Fallback: extract glyphs from dict (span level) when rawdict fails."""
    glyphs: list[GlyphObject] = []

    page_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)

    for block in page_dict.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                font_name = span.get("font", "")
                font_size = span.get("size", 0.0)
                flags = span.get("flags", 0)
                color = span.get("color", 0)
                text = span.get("text", "")
                bbox = span.get("bbox", (0, 0, 0, 0))
                is_bold = bool(flags & (1 << 4))

                if not text or any(d in font_name.lower() for d in _DECORATIVE_FONTS):
                    continue

                # Distribute bbox across characters
                char_width = (bbox[2] - bbox[0]) / max(len(text), 1)
                for j, ch in enumerate(text):
                    props = classify_devanagari_char(ch)
                    glyph = GlyphObject(
                        unicode_char=ch,
                        raw_char=ch,
                        x0=bbox[0] + j * char_width,
                        y0=bbox[1],
                        x1=bbox[0] + (j + 1) * char_width,
                        y1=bbox[3],
                        origin_x=bbox[0] + j * char_width,
                        origin_y=bbox[3],
                        font_name=font_name,
                        font_size=font_size,
                        is_bold=is_bold,
                        color=color,
                        extraction_source="pymupdf_dict",
                        is_devanagari=props.get("is_devanagari", False),
                        is_matra=props.get("is_matra", False),
                        is_consonant=props.get("is_consonant", False),
                        is_vowel=props.get("is_vowel", False),
                        is_halant=props.get("is_halant", False),
                    )
                    if is_legacy_font(font_name):
                        glyph.confidence = GlyphConfidence.SUSPICIOUS
                        glyph.unicode_confidence = 30.0
                    else:
                        glyph.confidence = GlyphConfidence.TRUSTED
                        glyph.unicode_confidence = 80.0
                    glyphs.append(glyph)

    return glyphs


def validate_page_glyphs(glyphs: list[GlyphObject]) -> list[GlyphObject]:
    """
    Validate Unicode sequences across all glyphs on a page.

    Checks every adjacent pair of Devanagari characters for sequence validity.
    Marks invalid glyphs and attempts repair.
    """
    if not glyphs:
        return glyphs

    for i in range(1, len(glyphs)):
        prev = glyphs[i - 1]
        curr = glyphs[i]

        # Only validate Devanagari sequences
        if not (curr.is_devanagari and prev.is_devanagari):
            continue

        # Check if on same line (vertical overlap)
        if abs(curr.origin_y - prev.origin_y) > curr.height * 0.5:
            continue  # Different line

        if not is_valid_devanagari_sequence(prev.unicode_char, curr.unicode_char):
            curr.is_valid_sequence = False
            curr.confidence = GlyphConfidence.INVALID
            curr.unicode_confidence = min(curr.unicode_confidence, 40.0)
            logger.debug(
                "Invalid sequence at pos %d: U+%04X + U+%04X",
                i, ord(prev.unicode_char), ord(curr.unicode_char)
            )

    return glyphs


def assemble_words(glyphs: list[GlyphObject]) -> list[WordObject]:
    """
    Assemble glyphs into words based on spatial proximity.

    Words are separated by spaces, large gaps, or line breaks.
    """
    if not glyphs:
        return []

    words: list[WordObject] = []
    current_word_glyphs: list[GlyphObject] = []

    for i, glyph in enumerate(glyphs):
        if not current_word_glyphs:
            current_word_glyphs.append(glyph)
            continue

        prev = current_word_glyphs[-1]

        # New line detection
        if abs(glyph.origin_y - prev.origin_y) > prev.height * 0.5:
            if current_word_glyphs:
                word = WordObject(glyphs=current_word_glyphs)
                word.compute_bbox()
                words.append(word)
            current_word_glyphs = [glyph]
            continue

        # Space or large gap detection
        gap = glyph.x0 - prev.x1
        avg_char_width = max(1.0, prev.width)

        if glyph.unicode_char.isspace() or gap > avg_char_width * 0.5:
            if current_word_glyphs:
                word = WordObject(glyphs=current_word_glyphs)
                word.compute_bbox()
                words.append(word)
            if glyph.unicode_char.isspace():
                current_word_glyphs = []
            else:
                current_word_glyphs = [glyph]
            continue

        current_word_glyphs.append(glyph)

    if current_word_glyphs:
        word = WordObject(glyphs=current_word_glyphs)
        word.compute_bbox()
        words.append(word)

    # Validate and repair each word
    for word in words:
        raw = word.raw_text
        if _DEVANAGARI_RE.search(raw):
            repaired, conf, reason = validate_and_repair_word(raw)
            word.confidence = conf
            if repaired != raw and reason:
                # Apply repair to individual glyphs
                word.is_valid_nepali = conf >= 70
                logger.debug("Word repair: %r → %r (%s)", raw, repaired, reason)

    return words
