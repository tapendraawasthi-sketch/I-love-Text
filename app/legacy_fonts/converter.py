"""
Core conversion engine for legacy Nepali fonts to Unicode.
Uses npttf2utf library for accurate, battle-tested mappings.
"""
from functools import lru_cache
from typing import Optional

from app.legacy_fonts.mappings import is_legacy_font, get_npttf2utf_map_name

try:
    from npttf2utf import npttf2utf
    _HAS_NPTTF2UTF = True
except ImportError:
    _HAS_NPTTF2UTF = False


@lru_cache(maxsize=32)
def _get_converter(map_name: str):
    """
    Returns a cached npttf2utf converter instance for the given map name.
    """
    if not _HAS_NPTTF2UTF:
        return None
    try:
        converter = npttf2utf.FontConverter(map_name)
        return converter
    except Exception:
        return None


def convert_legacy_text(text: str, font_name: str) -> str:
    """
    Converts legacy-font-encoded text to proper Unicode Devanagari.
    
    Args:
        text: The raw text extracted from the PDF/DOCX (ASCII-encoded Devanagari)
        font_name: The font name detected from the document
        
    Returns:
        Unicode Devanagari text
    """
    if not text or not text.strip():
        return text

    if not is_legacy_font(font_name):
        return text

    map_name = get_npttf2utf_map_name(font_name)
    converter = _get_converter(map_name)

    if converter is None:
        # If npttf2utf is not available, return raw text with a marker
        return text

    try:
        converted = converter.convert(text)
        return converted if converted else text
    except Exception:
        return text


def smart_convert(text: str, font_name: str) -> str:
    """Alias for convert_legacy_text for backward compatibility."""
    return convert_legacy_text(text, font_name)
