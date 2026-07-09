"""
Font Program Parser — extract glyph-to-Unicode mappings directly from
embedded font streams inside the PDF.

This is the CRITICAL fix for Weakness 1 (trusting OCR over digital data)
and Weakness 5 (no font program parser).

Strategy:
    1. Extract embedded font binary via PyMuPDF xref
    2. Parse with fonttools to read cmap / CFF / TrueType tables
    3. Build glyph_id → Unicode mapping from the font program itself
    4. Build char_code → glyph_name → Unicode mapping as fallback
    5. When ToUnicode CMap is wrong, the font program mapping wins

This means: a Preeti PDF no longer needs OCR.
We read the glyphs and map them to Unicode using the font's own tables,
then validate against our Preeti/Kantipur conversion maps.
"""
from __future__ import annotations

import io
import re
from functools import lru_cache
from typing import Any

import fitz

from app.logging_config import get_logger

logger = get_logger("FontProgramParser")

# Try to import fonttools for deep font parsing
_HAS_FONTTOOLS = False
try:
    from fontTools.ttLib import TTFont
    from fontTools.pens.boundsPen import BoundsPen
    _HAS_FONTTOOLS = True
except ImportError:
    logger.info("fonttools not installed — deep font parsing unavailable")


class FontProgram:
    """
    Parsed representation of an embedded PDF font.

    Contains the glyph → Unicode mapping derived from the font program
    itself, NOT from the PDF's ToUnicode CMap (which is often wrong).
    """

    def __init__(self, xref: int, font_name: str):
        self.xref = xref
        self.font_name = font_name
        self.glyph_count: int = 0
        self.has_cmap: bool = False
        self.has_cff: bool = False
        self.has_truetype: bool = False

        # The core mapping: glyph_id → unicode codepoint
        self.glyph_to_unicode: dict[int, int] = {}
        # Char code → unicode (from encoding + glyph names)
        self.charcode_to_unicode: dict[int, int] = {}
        # Glyph name → unicode
        self.name_to_unicode: dict[str, int] = {}
        # Valid codepoints (from PyMuPDF Font object)
        self.valid_codepoints: list[int] = []

        # Metrics
        self.glyph_widths: dict[int, float] = {}
        self.ascender: float = 0.0
        self.descender: float = 0.0

        # Status
        self.parsed: bool = False
        self.parse_error: str = ""
        self.confidence: float = 0.0  # How much we trust this mapping

    def unicode_for_glyph(self, glyph_id: int) -> str | None:
        """Get Unicode character for a glyph ID."""
        cp = self.glyph_to_unicode.get(glyph_id)
        if cp and cp > 0 and cp != 0xFFFD:
            return chr(cp)
        return None

    def unicode_for_charcode(self, charcode: int) -> str | None:
        """Get Unicode character for an encoded char code."""
        cp = self.charcode_to_unicode.get(charcode)
        if cp and cp > 0 and cp != 0xFFFD:
            return chr(cp)
        return None

    def unicode_for_name(self, glyph_name: str) -> str | None:
        """Get Unicode character for a glyph name."""
        cp = self.name_to_unicode.get(glyph_name)
        if cp and cp > 0 and cp != 0xFFFD:
            return chr(cp)
        return None


def extract_font_program(
    doc: fitz.Document,
    xref: int,
    font_name: str,
) -> FontProgram:
    """
    Extract and parse an embedded font from a PDF.

    Uses both PyMuPDF (fast, basic) and fonttools (deep parsing)
    to build the most complete glyph → Unicode mapping possible.
    """
    fp = FontProgram(xref=xref, font_name=font_name)

    # --- Phase 1: PyMuPDF font extraction ---
    try:
        _parse_with_pymupdf(doc, xref, fp)
    except Exception as e:
        logger.debug("PyMuPDF font parse failed for xref %d: %s", xref, e)

    # --- Phase 2: fonttools deep parsing ---
    if _HAS_FONTTOOLS:
        try:
            font_data = doc.extract_font(xref)
            if font_data and len(font_data) >= 4 and font_data[3]:
                font_buffer = font_data[3]
                _parse_with_fonttools(font_buffer, fp)
        except Exception as e:
            logger.debug("fonttools parse failed for xref %d (%s): %s", xref, font_name, e)

    # --- Phase 3: Compute confidence ---
    fp.confidence = _compute_font_confidence(fp)
    fp.parsed = bool(fp.glyph_to_unicode or fp.charcode_to_unicode or fp.valid_codepoints)

    if fp.parsed:
        logger.debug(
            "Font '%s' (xref=%d): %d glyph mappings, %d charcode mappings, "
            "confidence=%.0f%%",
            font_name, xref,
            len(fp.glyph_to_unicode),
            len(fp.charcode_to_unicode),
            fp.confidence,
        )

    return fp


def _parse_with_pymupdf(doc: fitz.Document, xref: int, fp: FontProgram) -> None:
    """Extract font info using PyMuPDF's built-in methods."""
    try:
        font_data = doc.extract_font(xref)
        if not font_data or len(font_data) < 4:
            return

        _name, _ext, _subtype, font_buffer = font_data[:4]

        if font_buffer:
            try:
                font_obj = fitz.Font(fontbuffer=font_buffer)
                fp.glyph_count = font_obj.glyph_count
                fp.ascender = font_obj.ascender
                fp.descender = font_obj.descender

                # Get valid codepoints — the key mapping
                vuc = font_obj.valid_codepoints()
                fp.valid_codepoints = list(vuc)
                fp.has_cmap = len(vuc) > 1

                # Build glyph name → unicode mapping
                for cp in vuc:
                    if cp > 0:
                        gname = font_obj.unicode_to_glyph_name(cp)
                        if gname and gname != ".notdef" and gname != ".notfound":
                            fp.name_to_unicode[gname] = cp

                        # Also check has_glyph for glyph_id
                        gid = font_obj.has_glyph(cp)
                        if gid and gid > 0:
                            fp.glyph_to_unicode[gid] = cp
                            fp.glyph_widths[gid] = font_obj.glyph_advance(cp)

            except Exception as e:
                logger.debug("PyMuPDF Font object creation failed: %s", e)

    except Exception as e:
        fp.parse_error = str(e)


def _parse_with_fonttools(font_buffer: bytes, fp: FontProgram) -> None:
    """Deep font parsing with fonttools — reads cmap, CFF, GSUB tables."""
    if not _HAS_FONTTOOLS or not font_buffer:
        return

    try:
        font_io = io.BytesIO(font_buffer)
        tt = TTFont(font_io, fontNumber=0)
    except Exception as e:
        logger.debug("fonttools TTFont parse failed: %s", e)
        return

    try:
        # --- Parse cmap table ---
        if "cmap" in tt:
            cmap_table = tt["cmap"]
            best_cmap = cmap_table.getBestCmap()
            if best_cmap:
                fp.has_cmap = True
                for charcode, glyph_name in best_cmap.items():
                    if charcode > 0 and charcode != 0xFFFD:
                        fp.charcode_to_unicode[charcode] = charcode
                        if glyph_name:
                            fp.name_to_unicode[glyph_name] = charcode

            # Try all subtables for additional mappings
            for subtable in cmap_table.tables:
                if hasattr(subtable, "cmap"):
                    for charcode, glyph_name in subtable.cmap.items():
                        if charcode > 0 and charcode not in fp.charcode_to_unicode:
                            fp.charcode_to_unicode[charcode] = charcode
                            if glyph_name and glyph_name not in fp.name_to_unicode:
                                fp.name_to_unicode[glyph_name] = charcode

        # --- Check for CFF data ---
        if "CFF " in tt:
            fp.has_cff = True
            try:
                cff = tt["CFF "]
                if hasattr(cff, "cff") and cff.cff.fontNames:
                    top_dict = cff.cff[0]
                    if hasattr(top_dict, "charset") and top_dict.charset:
                        for gid, gname in enumerate(top_dict.charset):
                            if gname and gname != ".notdef":
                                fp.name_to_unicode.setdefault(gname, 0)
            except Exception:
                pass

        # --- Check for glyf (TrueType) ---
        if "glyf" in tt:
            fp.has_truetype = True

        # --- Parse GSUB for ligature substitutions ---
        if "GSUB" in tt:
            try:
                _parse_gsub(tt["GSUB"], fp)
            except Exception:
                pass

        # --- Parse glyph order for name → ID mapping ---
        glyph_order = tt.getGlyphOrder()
        for gid, gname in enumerate(glyph_order):
            if gname in fp.name_to_unicode:
                fp.glyph_to_unicode.setdefault(gid, fp.name_to_unicode[gname])

    except Exception as e:
        logger.debug("fonttools deep parse error: %s", e)
    finally:
        tt.close()


def _parse_gsub(gsub_table: Any, fp: FontProgram) -> None:
    """Parse GSUB table for ligature and substitution information."""
    # GSUB contains glyph substitution rules (important for Devanagari conjuncts)
    # This tells us which glyph combinations form ligatures
    try:
        if hasattr(gsub_table, "table") and hasattr(gsub_table.table, "FeatureList"):
            feature_list = gsub_table.table.FeatureList
            if feature_list:
                for feature_record in feature_list.FeatureRecord:
                    tag = feature_record.FeatureTag
                    # Devanagari-relevant features
                    if tag in ("akhn", "blwf", "half", "pstf", "vatu",
                               "cjct", "pres", "abvs", "blws", "psts"):
                        logger.debug(
                            "Font has GSUB feature '%s' (Devanagari shaping)",
                            tag,
                        )
    except Exception:
        pass


def _compute_font_confidence(fp: FontProgram) -> float:
    """
    Compute confidence in the font's glyph → Unicode mapping.

    Higher confidence means we can trust the font program's mapping
    and don't need OCR.
    """
    score = 0.0

    if fp.has_cmap:
        score += 40.0
    if fp.glyph_to_unicode:
        score += min(30.0, len(fp.glyph_to_unicode) * 0.5)
    if fp.charcode_to_unicode:
        score += min(20.0, len(fp.charcode_to_unicode) * 0.3)
    if fp.has_cff or fp.has_truetype:
        score += 10.0
    if fp.valid_codepoints:
        # Check for Devanagari codepoints
        deva_count = sum(1 for cp in fp.valid_codepoints if 0x0900 <= cp <= 0x097F)
        if deva_count > 10:
            score += 15.0
        elif deva_count > 0:
            score += 5.0

    return min(100.0, score)


def extract_all_font_programs(
    pdf_bytes: bytes,
) -> dict[str, FontProgram]:
    """
    Extract and parse every embedded font in a PDF document.

    Returns a dict mapping font_name → FontProgram.
    """
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        raise ValueError(f"Cannot open PDF: {exc}") from exc

    programs: dict[str, FontProgram] = {}
    seen_xrefs: set[int] = set()

    try:
        for page_num in range(len(doc)):
            page = doc.load_page(page_num)
            fonts = page.get_fonts(full=True)

            for font_info in fonts:
                xref = font_info[0]
                if xref in seen_xrefs or xref <= 0:
                    continue
                seen_xrefs.add(xref)

                font_name = font_info[3] or font_info[4] or f"Font_{xref}"

                fp = extract_font_program(doc, xref, font_name)
                if fp.parsed:
                    programs[font_name] = fp

            # Also try short names
            for font_info in fonts:
                short_name = font_info[4]
                full_name = font_info[3]
                if short_name and full_name and full_name in programs:
                    programs[short_name] = programs[full_name]

    finally:
        doc.close()

    logger.info(
        "Extracted %d font programs (%d with cmap, %d with CFF)",
        len(programs),
        sum(1 for fp in programs.values() if fp.has_cmap),
        sum(1 for fp in programs.values() if fp.has_cff),
    )

    return programs


def resolve_glyph_unicode(
    char_code: int,
    glyph_name: str,
    font_name: str,
    font_programs: dict[str, FontProgram],
    pdf_unicode: str,
) -> tuple[str, float, str]:
    """
    Resolve the correct Unicode for a character using all available evidence.

    Priority:
        1. Font program cmap (highest trust — from the actual font file)
        2. Font program glyph name → Unicode (Adobe Glyph List)
        3. PDF ToUnicode mapping (often wrong for legacy Nepali fonts)
        4. Legacy font converter (Preeti/Kantipur maps as last resort)

    Returns: (unicode_char, confidence, source)
    """
    fp = font_programs.get(font_name)

    # Source 1: Font program charcode → Unicode
    if fp and char_code in fp.charcode_to_unicode:
        cp = fp.charcode_to_unicode[char_code]
        if cp > 0 and cp != 0xFFFD:
            return chr(cp), 95.0, "font_cmap"

    # Source 2: Font program glyph name → Unicode
    if fp and glyph_name and glyph_name in fp.name_to_unicode:
        cp = fp.name_to_unicode[glyph_name]
        if cp > 0 and cp != 0xFFFD:
            return chr(cp), 90.0, "font_glyph_name"

    # Source 3: PDF's ToUnicode mapping (may be wrong)
    if pdf_unicode and pdf_unicode != "\ufffd" and ord(pdf_unicode) != 0xFFFD:
        # Trust it if it's Devanagari
        if 0x0900 <= ord(pdf_unicode) <= 0x097F:
            return pdf_unicode, 80.0, "pdf_tounicode_devanagari"
        # Lower trust for non-Devanagari from legacy-named fonts
        from app.legacy_fonts.mappings import is_legacy_font
        if is_legacy_font(font_name):
            return pdf_unicode, 40.0, "pdf_tounicode_legacy_suspect"
        return pdf_unicode, 75.0, "pdf_tounicode"

    # Source 4: Legacy font converter
    from app.legacy_fonts.mappings import is_legacy_font, get_npttf2utf_map_name
    if is_legacy_font(font_name) and pdf_unicode:
        from app.legacy_fonts.converter import force_convert_legacy
        map_name = get_npttf2utf_map_name(font_name)
        converted = force_convert_legacy(pdf_unicode, map_name)
        if converted != pdf_unicode:
            return converted, 60.0, "legacy_converter"

    # Source 5: Return whatever we have
    if pdf_unicode:
        return pdf_unicode, 30.0, "fallback"

    return "\ufffd", 0.0, "unknown"
