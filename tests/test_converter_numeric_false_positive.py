"""
Regression test: plain numeric strings must never be misdetected as
Preeti-encoded text.

Previously, `_PREETI_DATE_RE` (`^[@)!#$%&(*.+^=\\s\\d]+$`) matched any
string made up purely of digits/whitespace/separators -- including
perfectly ordinary numbers like "10", "500", or "2081-01-01" -- because
digits are part of that character class. Since this check ran *before*
the bare-digit passthrough check in `is_plain_ascii_text`, plain numbers
were wrongly classified as legacy-encoded and run through Preeti
conversion, corrupting numeric table cells (a very common case in
financial/legal Nepali documents).
"""
from __future__ import annotations

from app.legacy_fonts.converter import (
    force_convert_legacy,
    is_legacy_encoded,
    is_plain_ascii_text,
)


def test_plain_numbers_are_not_legacy_encoded():
    for s in ["10", "500", "2081-01-01", "12/34", "0", "150", "999999"]:
        assert is_plain_ascii_text(s) is True, s
        assert is_legacy_encoded(s) is False, s


def test_plain_numbers_pass_through_conversion_unchanged():
    for s in ["10", "500", "2081-01-01"]:
        assert force_convert_legacy(s, "preeti") == s


def test_real_preeti_text_still_detected_and_converted():
    # A genuine Preeti-encoded fragment (contains actual Preeti symbol
    # keys, not just digits) must still be detected and converted.
    preeti_sample = "g]kfn ;/sf/"
    assert is_legacy_encoded(preeti_sample) is True
    converted = force_convert_legacy(preeti_sample, "preeti")
    assert converted != preeti_sample
    assert "\u0928\u0947\u092a\u093e\u0932" in converted  # "नेपाल"
