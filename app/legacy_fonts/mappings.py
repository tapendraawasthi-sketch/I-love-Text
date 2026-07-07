"""
Legacy font registry and detection utilities.
Uses npttf2utf for actual conversion mappings.
"""

# All known legacy Nepali font name fragments (lowercase)
KNOWN_LEGACY_FONTS = {
    "preeti",
    "kantipur",
    "sagarmatha",
    "himali",
    "himalb",
    "pcs",
    "aakriti",
    "ganess",
    "ganesh",
    "fontasy",
    "kalimati_old",
    "kanchan",
    "navjeevan",
    "pcsnepali",
    "siddhi",
    "vishwash",
    "ekantipur",
}

# Map from our font fragment keys to npttf2utf map names
# npttf2utf supports: preeti, kantipur, sagarmatha, himali, pcsnepali, aakriti
NPTTF2UTF_FONT_MAP = {
    "preeti": "preeti",
    "kantipur": "kantipur",
    "ekantipur": "kantipur",
    "sagarmatha": "sagarmatha",
    "himali": "himali",
    "himalb": "himali",
    "pcs": "pcsnepali",
    "pcsnepali": "pcsnepali",
    "aakriti": "aakriti",
    "ganess": "preeti",
    "ganesh": "preeti",
    "fontasy": "preeti",
    "kanchan": "preeti",
    "navjeevan": "preeti",
    "siddhi": "preeti",
    "vishwash": "preeti",
}


def is_legacy_font(font_name: str) -> bool:
    """Check if font name matches a known legacy Nepali font."""
    if not font_name:
        return False
    fl = font_name.lower().strip()
    return any(legacy in fl for legacy in KNOWN_LEGACY_FONTS)


def get_npttf2utf_map_name(font_name: str) -> str:
    """
    Given a font name, return the npttf2utf map name to use.
    Defaults to 'preeti' if the font is legacy but not specifically mapped.
    """
    if not font_name:
        return "preeti"
    fl = font_name.lower().strip()
    for fragment, map_name in NPTTF2UTF_FONT_MAP.items():
        if fragment in fl:
            return map_name
    return "preeti"
