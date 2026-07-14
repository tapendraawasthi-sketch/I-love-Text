"""Accuracy / fidelity regression tests for maximum .txt fidelity."""
from __future__ import annotations

import pytest

from app.extract.fidelity import (
    allow_lexicon_repair,
    allow_mutations,
    get_fidelity,
    min_corruption_for_repair,
    normalize_fidelity,
    reset_fidelity,
    set_fidelity,
)
from app.extract.txt_formatter import format_as_txt
from app.legacy_fonts.converter import force_convert_legacy
from app.nlp.font_detector import guess_font_from_text
from app.nlp.nepali_sentence_intelligence import repair_corrupted_devanagari
from app.ocr.nepali_postprocess import normalize_nepali_text


def cer(hypothesis: str, reference: str) -> float:
    """Character Error Rate (Levenshtein / len(reference))."""
    if not reference:
        return 0.0 if not hypothesis else 1.0
    # classic DP
    a, b = hypothesis, reference
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i]
        for j, cb in enumerate(b, 1):
            curr.append(min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = curr
    return prev[-1] / len(b)


def test_normalize_fidelity_defaults():
    assert normalize_fidelity(None) == "forensic"
    assert normalize_fidelity("as-is") == "forensic"
    assert normalize_fidelity("ASSISTED") == "assisted"
    with pytest.raises(ValueError):
        normalize_fidelity("bogus")


def test_forensic_blocks_mutations():
    token = set_fidelity("forensic")
    try:
        assert get_fidelity() == "forensic"
        assert allow_mutations() is False
        assert allow_lexicon_repair() is False
        assert min_corruption_for_repair() > 1.0
    finally:
        reset_fidelity(token)


def test_assisted_allows_mutations():
    token = set_fidelity("assisted")
    try:
        assert allow_mutations() is True
        assert allow_lexicon_repair() is True
    finally:
        reset_fidelity(token)


def test_preeti_nepall_conversion_exact():
    # Classic Preeti encoding of नेपाल
    out = force_convert_legacy("g]kfn", "preeti")
    assert "नेपाल" in out
    assert cer(out.strip(), "नेपाल") <= 0.05


def test_unknown_font_guess_does_not_default_preeti():
    guess = guess_font_from_text("Hello World Example Document")
    assert guess["family"] == "unknown"


def test_preeti_pattern_guess_still_works():
    guess = guess_font_from_text("g]kfn ;/sf/ sf] sfof{no")
    assert guess["family"] in ("preeti", "kantipur")
    assert guess["confidence"] >= 55


def test_forensic_normalize_does_not_lexicon_swap():
    token = set_fidelity("forensic")
    try:
        # Unusual but valid-looking Devanagari should not become नियमवाली
        original = "कम्पनीको विशेष नामअअअ"
        out = normalize_nepali_text(original)
        assert "नियमवाली" not in out
        assert "कम्पनी" in out or out  # still returns text
    finally:
        reset_fidelity(token)


def test_repair_without_lexicon_strips_pua_only():
    from app.nlp.nepali_sentence_intelligence import CORRUPTION_RE
    text = "नेपाल\ue000सरकार"
    # Marker-stripped path used by forensic/balanced (no lexicon)
    out = CORRUPTION_RE.sub("", text)
    assert "\ue000" not in out
    assert "नेपाल" in out


def test_txt_formatter_page_break_normalization():
    result = {
        "text": "पहिलो पृष्ठ\n\n--- Page Break ---\n\nदोस्रो पृष्ठ",
        "pages": 2,
        "method": "document_intelligence",
        "fidelity": "forensic",
        "mean_confidence": 99.0,
    }
    out = format_as_txt(result, include_page_separators=True, include_quality_report=False)
    assert "PAGE 1" in out
    assert "PAGE 2" in out
    assert "--- Page Break ---" not in out
    assert "पहिलो पृष्ठ" in out
    assert "दोस्रो पृष्ठ" in out


def test_txt_formatter_quality_report_lists_fidelity():
    result = {
        "text": "नमस्ते",
        "pages": 1,
        "method": "direct_legacy",
        "fidelity": "forensic",
        "mean_confidence": 98.5,
        "corrections": [],
    }
    out = format_as_txt(result, include_quality_report=True)
    assert "Fidelity: forensic" in out
