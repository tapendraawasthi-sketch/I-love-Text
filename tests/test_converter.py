import pytest
from app.legacy_fonts.mappings import is_legacy_font, get_npttf2utf_map_name
from app.legacy_fonts.converter import convert_legacy_text


def test_is_legacy_font():
    assert is_legacy_font("Preeti") is True
    assert is_legacy_font("preeti-bold") is True
    assert is_legacy_font("Kantipur") is True
    assert is_legacy_font("Sagarmatha") is True
    assert is_legacy_font("Himali") is True
    assert is_legacy_font("Aakriti") is True
    assert is_legacy_font("PCS Nepali") is True
    assert is_legacy_font("Arial") is False
    assert is_legacy_font("Mangal") is False
    assert is_legacy_font("") is False
    assert is_legacy_font(None) is False


def test_get_map_name():
    assert get_npttf2utf_map_name("Preeti") == "preeti"
    assert get_npttf2utf_map_name("Kantipur") == "kantipur"
    assert get_npttf2utf_map_name("ekantipur") == "kantipur"
    assert get_npttf2utf_map_name("Sagarmatha") == "sagarmatha"
    assert get_npttf2utf_map_name("Aakriti") == "aakriti"


def test_non_legacy_passthrough():
    text = "Hello World 123"
    assert convert_legacy_text(text, "Arial") == text
    assert convert_legacy_text(text, "Mangal") == text


def test_preeti_conversion():
    """Test that Preeti-encoded text gets converted to Devanagari."""
    result = convert_legacy_text("g]kfn", "Preeti")
    # Should produce Nepal in Devanagari
    assert result != "g]kfn"  # Must not be the same ASCII
    assert any(ord(c) >= 0x0900 for c in result)  # Must contain Devanagari


def test_empty_text():
    assert convert_legacy_text("", "Preeti") == ""
    assert convert_legacy_text("   ", "Preeti") == "   "
