"""
Regression guard: no Python source file in `app/` should contain a
Devanagari-*looking* string literal that is actually mojibake.

This test exists because app/extract/unicode_validator.py previously shipped
a `_COMMON_WORDS` set whose entries had been corrupted by a lossy
encode/decode round-trip (real Nepali UTF-8 bytes decoded through the wrong
codec and re-saved). The corrupted set silently matched nothing, so a whole
word-validation fallback path did nothing without ever raising an error.

This check is intentionally simple and cheap: for every .py file under
`app/`, it re-encodes/decodes the file's text as UTF-8 (which will always
succeed once Python has already parsed it, so that alone doesn't help) and
instead scans for the specific mojibake byte patterns that a UTF-8-as-
Latin-1/CP437 double-encoding produces, and flags any file containing
Devanagari string literals mixed with those tell-tale mojibake byte
sequences.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parent.parent / "app"

# Devanagari block, for sanity-checking that files *do* contain real
# Devanagari where expected.
_DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]")

# Tell-tale characters that show up when UTF-8 encoded Devanagari text is
# incorrectly decoded as CP437/Latin-1/CP1252 and then re-saved as UTF-8.
# These characters have no legitimate reason to appear inside Python source
# comments/strings in this codebase.
_MOJIBAKE_MARKERS = [
    "\u0393\u00c7",  # "ΓÇ" -- seen from mis-decoded em-dash / smart quotes
    "\u0393\u00e5",  # "Γå" -- seen from mis-decoded arrow characters
    "\u03b1\u00f1",  # "αñ" -- seen from mis-decoded Devanagari consonants
    "\u03b1\u00d1",  # "αÑ" -- seen from mis-decoded Devanagari vowel signs
]


def _iter_py_files():
    return sorted(APP_ROOT.rglob("*.py"))


@pytest.mark.parametrize("path", _iter_py_files(), ids=lambda p: str(p.relative_to(APP_ROOT.parent)))
def test_no_mojibake_markers_in_source(path: Path):
    text = path.read_text(encoding="utf-8", errors="strict")
    found = [m for m in _MOJIBAKE_MARKERS if m in text]
    assert not found, (
        f"{path} contains mojibake marker sequence(s) {found!r}. "
        "This usually means a Devanagari string literal was corrupted by a "
        "lossy encode/decode round-trip (e.g. UTF-8 bytes decoded as "
        "CP437/Latin-1 and re-saved). Fix the source encoding before "
        "committing -- see app/extract/unicode_validator.py git history "
        "for a real example of this exact failure mode."
    )


def test_known_vocabulary_files_contain_real_devanagari():
    """
    Sanity check the other direction: files that are *supposed* to hold
    Nepali vocabulary actually contain real Devanagari characters, so this
    test suite would fail loudly if someone accidentally emptied or
    corrupted them in a way the marker-scan above wouldn't catch.
    """
    kb_path = APP_ROOT / "intelligence" / "nepal_knowledge_base.py"
    if not kb_path.exists():
        pytest.skip("nepal_knowledge_base.py not present")
    text = kb_path.read_text(encoding="utf-8")
    assert _DEVANAGARI_RE.search(text), (
        "Expected real Devanagari characters in nepal_knowledge_base.py "
        "but found none -- vocabulary may have been corrupted or removed."
    )
