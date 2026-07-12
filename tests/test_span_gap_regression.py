"""
Regression test for a span-joining bug in extract_page_direct() found via
forensic audit of a real production output file.

BUG: when joining adjacent PyMuPDF text spans on the same line, the gap
between them was computed as `gap = x0_current - x0_previous` (start-to-
start distance) instead of the true visual gap `x0_current - x1_previous`
(end of previous span to start of current span). Start-to-start distance
includes the *width* of the previous span, so any span wider than the
5pt space-insertion threshold -- e.g. a completely ordinary 2-character
consonant+matra run like "कु" -- caused a false space to be inserted
before the next span even when the two spans were visually touching.

This was silent and systematic: it reproduced identically every time the
word "कुल" ("total") appeared in a real extracted document, always
rendering as "कु ल" (with a spurious space splitting the word in half),
and would affect any other syllable/word combination where PDF span
splitting happens to produce a multi-character span wider than ~5pt
immediately followed by another span.
"""
from __future__ import annotations

import unittest.mock as mock

import fitz

from app.extract.direct_extract import extract_page_direct


def _extract_with_fake_spans(spans: list[dict]) -> str:
    fake_dict = {
        "blocks": [
            {"type": 0, "lines": [{"spans": spans}]}
        ]
    }

    def fake_get_text(self, mode="text", **kwargs):
        if mode == "dict":
            return fake_dict
        if mode == "words":
            return []
        return ""

    doc = fitz.open()
    page = doc.new_page()
    with mock.patch.object(fitz.Page, "get_text", fake_get_text):
        result = extract_page_direct(page)
    return result.get("text", "")


def test_touching_multichar_span_does_not_insert_spurious_space():
    """
    The exact real-world failure case: "कु" as its own 18pt-wide span,
    immediately (1pt gap) followed by "ल" -- must join as "कुल", not
    "कु ल".
    """
    spans = [
        {"font": "ArialUnicodeMS", "text": "कु", "bbox": (40.0, 20.0, 58.0, 34.0)},
        {"font": "ArialUnicodeMS", "text": "ल", "bbox": (59.0, 20.0, 68.0, 34.0)},
    ]
    text = _extract_with_fake_spans(spans)
    assert text == "कुल", f"expected 'कुल' but got {text!r}"


def test_genuinely_separated_words_still_get_a_space():
    """A real word-gap (> 5pt visual gap between spans) must still insert
    a space -- this fix must not collapse legitimate word boundaries."""
    spans = [
        {"font": "ArialUnicodeMS", "text": "राम", "bbox": (40.0, 20.0, 60.0, 34.0)},
        {"font": "ArialUnicodeMS", "text": "श्याम", "bbox": (75.0, 20.0, 100.0, 34.0)},  # 15pt real gap
    ]
    text = _extract_with_fake_spans(spans)
    assert text == "राम श्याम"


def test_genuinely_separated_columns_still_get_a_separator():
    """A large real gap (> 50pt) between spans must still insert some
    separator rather than concatenating the spans together (the raw
    tab character is later normalized to a space by _fix_common_errors,
    which is separate, pre-existing, and intentional behavior)."""
    spans = [
        {"font": "ArialUnicodeMS", "text": "राम", "bbox": (40.0, 20.0, 60.0, 34.0)},
        {"font": "ArialUnicodeMS", "text": "100", "bbox": (200.0, 20.0, 220.0, 34.0)},  # 140pt real gap
    ]
    text = _extract_with_fake_spans(spans)
    assert text == "राम 100"
    assert "राम100" not in text
