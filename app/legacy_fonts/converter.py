"""
Core conversion engine for legacy Nepali fonts to Unicode.

Tries npttf2utf library first, falls back to built-in Preeti mapping.
"""
from functools import lru_cache
import logging

from app.legacy_fonts.mappings import is_legacy_font, get_npttf2utf_map_name
from app.legacy_fonts.preeti_map import preeti_to_unicode, conversion_quality

logger = logging.getLogger("LegacyFontConverter")

# Try to import npttf2utf
try:
    from npttf2utf import npttf2utf
    _HAS_NPTTF2UTF = True
    logger.info("npttf2utf library loaded successfully")
except ImportError as e:
    _HAS_NPTTF2UTF = False
    logger.warning(f"npttf2utf not available: {e}")


@lru_cache(maxsize=32)
def _get_npttf2utf_converter(map_name: str):
    """Returns a cached npttf2utf converter instance."""
    if not _HAS_NPTTF2UTF:
        return None
    try:
        converter = npttf2utf.FontConverter(map_name)
        return converter
    except Exception as e:
        logger.warning(f"Failed to create npttf2utf converter for {map_name}: {e}")
        return None


def _convert_with_npttf2utf(text: str, map_name: str) -> str | None:
    """Try conversion with npttf2utf, return None if fails."""
    converter = _get_npttf2utf_converter(map_name)
    if converter is None:
        return None
    
    try:
        result = converter.convert(text)
        if result and result != text:
            # Verify conversion quality
            quality = conversion_quality(text, result)
            if quality["devanagari_ratio"] >= 20:
                return result
            logger.warning(f"npttf2utf produced low quality output ({quality['devanagari_ratio']}% Devanagari)")
        return None
    except Exception as e:
        logger.warning(f"npttf2utf conversion error: {e}")
        return None


def _convert_with_builtin(text: str) -> str:
    """Convert using built-in Preeti mapping."""
    result = preeti_to_unicode(text)
    quality = conversion_quality(text, result)
    logger.info(f"Built-in conversion: {quality['devanagari_ratio']}% Devanagari")
    return result


def convert_legacy_text(text: str, font_name: str) -> str:
    """
    Converts legacy-font-encoded text to proper Unicode Devanagari.
    
    Uses npttf2utf if available and working, falls back to built-in mapping.
    
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
    
    # Try npttf2utf first
    if _HAS_NPTTF2UTF:
        result = _convert_with_npttf2utf(text, map_name)
        if result:
            return result
        logger.info(f"npttf2utf failed for {font_name}, using built-in converter")
    
    # Fallback to built-in Preeti converter
    return _convert_with_builtin(text)


def smart_convert(text: str, font_name: str) -> str:
    """Alias for convert_legacy_text for backward compatibility."""
    return convert_legacy_text(text, font_name)


def check_conversion_status() -> dict:
    """Check conversion capabilities for diagnostics."""
    status = {
        "npttf2utf_available": _HAS_NPTTF2UTF,
        "builtin_available": True,
        "supported_fonts": ["preeti", "kantipur", "sagarmatha", "himali", "aakriti", "siddhi"],
    }
    
    if _HAS_NPTTF2UTF:
        # Test npttf2utf
        try:
            conv = _get_npttf2utf_converter("preeti")
            if conv:
                test = conv.convert("g]kfn")  # "nepal" in Preeti
                status["npttf2utf_test"] = test
                status["npttf2utf_working"] = "नेपाल" in test or len(test) < len("g]kfn")
            else:
                status["npttf2utf_working"] = False
        except Exception as e:
            status["npttf2utf_error"] = str(e)
            status["npttf2utf_working"] = False
    
    # Test built-in
    builtin_test = preeti_to_unicode("g]kfn")
    status["builtin_test"] = builtin_test
    
    return status
