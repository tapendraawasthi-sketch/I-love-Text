"""
Core conversion engine for legacy Nepali fonts to Unicode.

Uses npttf2utf FontMapper when available, falls back to built-in Preeti mapping.
"""
from __future__ import annotations

import os
import re
from functools import lru_cache
import logging

from app.legacy_fonts.mappings import is_legacy_font, get_npttf2utf_map_name
from app.legacy_fonts.preeti_map import preeti_to_unicode, conversion_quality, is_likely_preeti

logger = logging.getLogger("LegacyFontConverter")

_PLAIN_ASCII_RE = re.compile(r"^[a-zA-Z0-9._:/\-\s]+$")
_PREETI_DATE_RE = re.compile(r"^[@)!#$%&(*.+^=\s\d]+$")
_DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]")

# Try to import npttf2utf (API changed in 0.3.x)
_HAS_NPTTF2UTF = False
_FontMapper = None
_MAP_JSON_PATH = None

try:
    import npttf2utf
    from npttf2utf import FontMapper as _FontMapperCls

    _MAP_JSON_PATH = os.path.join(os.path.dirname(npttf2utf.__file__), "map.json")
    _FontMapper = _FontMapperCls
    _HAS_NPTTF2UTF = os.path.isfile(_MAP_JSON_PATH)
    if _HAS_NPTTF2UTF:
        logger.info("npttf2utf FontMapper loaded from %s", _MAP_JSON_PATH)
except Exception as e:
    logger.warning("npttf2utf not available: %s", e)


_FONT_DISPLAY_NAMES = {
    "preeti": "Preeti",
    "kantipur": "Kantipur",
    "sagarmatha": "Sagarmatha",
    "himali": "Himali",
    "aakriti": "Aakriti",
    "pcsnepali": "PCS Nepali",
}


@lru_cache(maxsize=8)
def _get_font_mapper():
    if not _HAS_NPTTF2UTF or _FontMapper is None or not _MAP_JSON_PATH:
        return None
    try:
        return _FontMapper(_MAP_JSON_PATH)
    except Exception as exc:
        logger.warning("Failed to create FontMapper: %s", exc)
        return None


def is_plain_ascii_text(text: str) -> bool:
    """URLs, emails, and plain English — never run through Preeti conversion."""
    stripped = text.strip()
    if not stripped:
        return True

    # Preeti-encoded dates like @)#!.$.!* must NOT be treated as plain ASCII.
    if _PREETI_DATE_RE.match(stripped):
        return False

    if "www." in stripped or "http://" in stripped or "https://" in stripped:
        return True

    # Page numbers and bare digits — never convert.
    if re.fullmatch(r"\d+\s*", stripped):
        return True

    # Real email addresses only (letters after @), not Preeti date codes.
    if re.search(r"@[a-zA-Z]", stripped) and "." in stripped:
        return True

    return bool(_PLAIN_ASCII_RE.match(stripped))


def is_legacy_encoded(text: str) -> bool:
    """
    Return True when text looks like legacy font encoding (Preeti/Kantipur ASCII),
    False when it is already Unicode Devanagari or plain ASCII Latin.
    """
    if not text or not text.strip():
        return False

    if is_plain_ascii_text(text):
        return False

    chars = [c for c in text if c.strip()]
    if not chars:
        return False

    devanagari = sum(1 for c in chars if "\u0900" <= c <= "\u097F")
    ascii_letters = sum(1 for c in chars if c.isascii() and c.isalpha())
    deva_ratio = devanagari / len(chars)
    ascii_ratio = ascii_letters / len(chars)

    if deva_ratio >= 0.50 and ascii_ratio < 0.15:
        return False

    if _PREETI_DATE_RE.match(text.strip()):
        return True

    if is_likely_preeti(text):
        return True

    return ascii_ratio >= 0.08 or any(c in text for c in "]}/;{_|")


def _convert_with_npttf2utf(text: str, map_name: str) -> str | None:
    mapper = _get_font_mapper()
    if mapper is None:
        return None

    from_font = _FONT_DISPLAY_NAMES.get(map_name, map_name.title())
    try:
        result = mapper.map_to_unicode(text, from_font=from_font)
        if result and result != text:
            quality = conversion_quality(text, result)
            if quality["devanagari_ratio"] >= 10 or _PREETI_DATE_RE.match(text.strip()):
                return result
            logger.warning(
                "npttf2utf low quality (%.1f%% Devanagari) for map %s",
                quality["devanagari_ratio"], map_name,
            )
    except Exception as exc:
        logger.warning("npttf2utf conversion error (%s): %s", map_name, exc)
    return None


def _convert_with_builtin(text: str) -> str:
    result = preeti_to_unicode(text)
    quality = conversion_quality(text, result)
    logger.debug("Built-in conversion: %s%% Devanagari", quality["devanagari_ratio"])
    return result


def force_convert_legacy(text: str, map_name: str) -> str:
    """Convert using a specific map. Caller must ensure text needs conversion."""
    if not text or not text.strip():
        return text
    if is_plain_ascii_text(text):
        return text

    result = _convert_with_npttf2utf(text, map_name)
    if result:
        return result
    return _convert_with_builtin(text)


def convert_legacy_text(text: str, font_name: str) -> str:
    """Convert legacy-font-encoded text to Unicode when needed."""
    if not text or not text.strip():
        return text
    if is_plain_ascii_text(text):
        return text
    if not is_legacy_encoded(text) and not is_legacy_font(font_name):
        return text
    if not is_legacy_font(font_name):
        return text

    map_name = get_npttf2utf_map_name(font_name)
    return force_convert_legacy(text, map_name)


def smart_convert(text: str, font_name: str) -> str:
    return convert_legacy_text(text, font_name)


def check_conversion_status() -> dict:
    status = {
        "npttf2utf_available": _HAS_NPTTF2UTF,
        "builtin_available": True,
        "supported_fonts": ["preeti", "kantipur", "sagarmatha", "himali", "aakriti", "siddhi"],
        "map_json_path": _MAP_JSON_PATH,
    }

    if _HAS_NPTTF2UTF:
        try:
            mapper = _get_font_mapper()
            if mapper:
                test = mapper.map_to_unicode("g]kfn", from_font="Preeti")
                status["npttf2utf_test"] = test
                status["npttf2utf_working"] = "नेपाल" in test
            else:
                status["npttf2utf_working"] = False
        except Exception as e:
            status["npttf2utf_error"] = str(e)
            status["npttf2utf_working"] = False

    status["builtin_test"] = preeti_to_unicode("g]kfn")
    return status
