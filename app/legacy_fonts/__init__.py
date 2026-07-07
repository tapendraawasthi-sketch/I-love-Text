"""
Legacy Nepali font to Unicode conversion engine.
Uses npttf2utf for accurate conversion of Preeti, Kantipur, 
Sagarmatha, Himali, PCS Nepali, Aakriti and other legacy fonts.
"""
from app.legacy_fonts.converter import convert_legacy_text, smart_convert, is_legacy_font

__all__ = ["convert_legacy_text", "smart_convert", "is_legacy_font"]
