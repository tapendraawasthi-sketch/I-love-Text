"""
Regression test for a gap in unicode_validator.py's doubled-combining-mark
collapse, found via forensic audit of a real production output file.

The existing _fix_double_matras() only covered post-base vowel signs
(matras, U+093E-U+094C). It did NOT cover chandrabindu (U+0901 ँ),
anusvara (U+0902 ं), or visarga (U+0903 ः) doubling at all, even though
these are combining marks with the exact same failure mode. Real examples
found in the audited document:

    पूँँजीगत   (should be पूँजीगत)   -- doubled chandrabindu
    संंकलन    (should be संकलन)     -- doubled anusvara
    संंस्था   (should be संस्था)    -- doubled anusvara
    अन्तःःशुल्क (should be अन्तःशुल्क) -- doubled visarga

These three marks never legitimately repeat back to back in standard
Nepali orthography, so collapsing consecutive duplicates is always safe.
"""
from __future__ import annotations

from app.extract.unicode_validator import _fix_double_matras, repair_devanagari_unicode


def test_doubled_chandrabindu_collapses():
    assert _fix_double_matras("पूँँजीगत") == "पूँजीगत"


def test_doubled_anusvara_collapses():
    assert _fix_double_matras("संंकलन") == "संकलन"
    assert _fix_double_matras("संंस्था") == "संस्था"
    assert _fix_double_matras("संंरचना") == "संरचना"


def test_doubled_visarga_collapses():
    assert _fix_double_matras("अन्तःःशुल्क") == "अन्तःशुल्क"


def test_single_nasalization_marks_are_left_alone():
    """Make sure the fix doesn't strip legitimate single occurrences."""
    assert _fix_double_matras("नेपाल सरकार") == "नेपाल सरकार"
    assert _fix_double_matras("संकलन") == "संकलन"
    assert _fix_double_matras("अन्तःशुल्क") == "अन्तःशुल्क"


def test_full_pipeline_repair_devanagari_unicode_also_fixes_it():
    """The full repair_devanagari_unicode() entry point (which calls
    _fix_double_matras internally) should also fix these cases end to
    end, not just the isolated helper."""
    text = repair_devanagari_unicode("पूँँजीगत संंकलन अन्तःःशुल्क")
    assert "पूँँ" not in text
    assert "संं" not in text
    assert "ःः" not in text
