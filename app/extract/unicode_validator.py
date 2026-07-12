"""
Unicode Validator and Repair Engine for Devanagari text.

This is the critical component that detects and fixes broken Unicode
sequences - one of the most common sources of errors in Nepali PDF
extraction.

Common problems:
    1. Matra reordering: a vowel sign placed after the wrong consonant
    2. Split conjuncts: halant+consonant sequences in the wrong order
    3. Orphan matras: a vowel sign appearing without a consonant base
    4. Double matras: a duplicated vowel sign
    5. Wrong NFC normalization

The validator works at three levels:
    1. Character level: Is this character valid after the previous one?
    2. Syllable level: Is this syllable a valid Devanagari syllable?
    3. Word level: Is this word a known Nepali word (or close to one)?

NOTE: word-level validation is delegated entirely to
app.intelligence.nepal_knowledge_base, which holds the actual (correctly
UTF-8 encoded) Nepali vocabulary. A previous version of this file kept a
local `_COMMON_WORDS` set as a "fallback" -- that set had been corrupted by
a lossy encode/decode round-trip (it contained mojibake, not real
Devanagari characters), so it could never match anything and silently did
nothing. It has been removed rather than fixed in place, since keeping two
separate word lists in sync is itself an error source; see
nepal_knowledge_base.py for the single source of truth.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

from app.extract.glyph_model import (
    GlyphObject, GlyphConfidence, WordObject,
    classify_devanagari_char, is_valid_devanagari_sequence,
    _DEVANAGARI_CONSONANTS, _DEVANAGARI_MATRAS, _DEVANAGARI_HALANT,
    _PREBASE_MATRAS,
)
from app.logging_config import get_logger

logger = get_logger("UnicodeValidator")

_DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]")


class UnicodeValidationResult:
    """Result of validating a Unicode string."""

    def __init__(self):
        self.is_valid: bool = True
        self.errors: list[dict[str, Any]] = []
        self.warnings: list[dict[str, Any]] = []
        self.repaired_text: str = ""
        self.original_text: str = ""
        self.repair_count: int = 0
        self.confidence: float = 100.0

    def add_error(self, position: int, char: str, message: str) -> None:
        self.errors.append({"pos": position, "char": char, "message": message})
        self.is_valid = False

    def add_warning(self, position: int, char: str, message: str) -> None:
        self.warnings.append({"pos": position, "char": char, "message": message})


def validate_devanagari_text(text: str) -> UnicodeValidationResult:
    """
    Validate a Devanagari Unicode string for correctness.

    Returns validation result with errors, warnings, and optional repair.
    """
    result = UnicodeValidationResult()
    result.original_text = text

    if not text:
        result.repaired_text = text
        return result

    # Normalize to NFC first
    normalized = unicodedata.normalize("NFC", text)

    chars = list(normalized)
    errors_found = False

    for i, char in enumerate(chars):
        if not _DEVANAGARI_RE.match(char):
            continue

        prev_char = chars[i - 1] if i > 0 else ""

        # Check sequence validity
        if not is_valid_devanagari_sequence(prev_char, char):
            result.add_error(i, char, f"Invalid sequence: '{prev_char}'+'{char}'")
            errors_found = True

        # Check for orphan matras at word boundaries
        props = classify_devanagari_char(char)
        if props.get("is_matra") and (i == 0 or not _DEVANAGARI_RE.match(prev_char)):
            result.add_error(i, char, "Orphan matra at word boundary")
            errors_found = True

    if errors_found:
        result.repaired_text = repair_devanagari_unicode(normalized)
        result.repair_count = len(result.errors)
        result.confidence = max(0, 100 - len(result.errors) * 5)
    else:
        result.repaired_text = normalized
        result.confidence = 100.0

    return result


def repair_devanagari_unicode(text: str) -> str:
    """
    Repair common Devanagari Unicode errors.

    This handles:
    1. Matra reordering (the most common error)
    2. Double matra removal
    3. Orphan matra cleanup
    4. Halant normalization
    5. NFC re-normalization after repair
    """
    if not text:
        return text

    text = unicodedata.normalize("NFC", text)

    # 1. Fix matra reordering: the U+093F (short i) matra and similar
    # pre-base vowel signs commonly end up attached to the wrong consonant
    # in broken PDF extractions -- matras get attached to the wrong
    # consonant in a conjunct instead of the intended one.

    # Fix double matras
    text = _fix_double_matras(text)

    # Fix orphan matras
    text = _fix_orphan_matras(text)

    # Fix common matra displacement patterns
    text = _fix_matra_displacement(text)

    # Fix halant issues
    text = _fix_halant_issues(text)

    # Re-normalize
    text = unicodedata.normalize("NFC", text)

    return text


def _fix_double_matras(text: str) -> str:
    """Remove duplicate consecutive matras."""
    matra_codepoints = "".join(chr(cp) for cp in _DEVANAGARI_MATRAS)
    # Remove consecutive duplicate matras
    pattern = f"([{re.escape(matra_codepoints)}])\\1+"
    return re.sub(pattern, r"\1", text)


def _fix_orphan_matras(text: str) -> str:
    """Remove matras that don't have a consonant base."""
    chars = list(text)
    result = []
    matra_cps = _DEVANAGARI_MATRAS | {_DEVANAGARI_HALANT}

    for i, char in enumerate(chars):
        cp = ord(char)
        if cp in matra_cps:
            # Check if preceded by consonant or nukta
            if i > 0:
                prev_cp = ord(chars[i - 1])
                if (prev_cp in _DEVANAGARI_CONSONANTS or
                        prev_cp == 0x093C or  # nukta
                        prev_cp in matra_cps):  # another matra (valid chain)
                    result.append(char)
                    continue
            # Orphan matra -- skip it
            logger.debug("Removing orphan matra U+%04X at position %d", cp, i)
            continue
        result.append(char)

    return "".join(result)


def _fix_matra_displacement(text: str) -> str:
    """
    Fix common matra displacement patterns in extracted text.

    The most critical repair: when PDF extraction places matras on wrong consonants.

    Example pattern: two consonants (C1, C2) followed by two vowel signs
    (U+0947, U+093E) both landing after C2, when they should be split one
    per consonant (C1 gets the first matra, C2 gets the second).

    Strategy: Use syllable structure analysis to detect impossible
    combinations (two vowel signs stacked on one consonant with no halant
    between them) and redistribute matras to their correct consonant bases.
    """
    fixes = [
        # C1 C2 + U+0947 + U+093E  ->  C1+U+0947  C2+U+093E
        (r"([\u0915-\u0939])([\u0915-\u0939])\u0947\u093E([\u0915-\u0939])",
         _redistribute_matras_3),

        # Double matra that should be split across consonants
        # pattern: C1 C2 + matra1 + matra2 -> C1+matra1 C2+matra2
        (r"([\u0915-\u0939])([\u0915-\u0939])([\u093E-\u094C])([\u093E-\u094C])",
         _redistribute_double_matra),
    ]

    result = text
    for pattern, handler in fixes:
        result = re.sub(pattern, handler, result)

    return result


def _redistribute_matras_3(match: re.Match) -> str:
    """Redistribute matras across three consonants."""
    c1, c2, c3 = match.group(1), match.group(2), match.group(3)
    # C1 C2 U+0947 U+093E C3  ->  C1 U+0947 C2 U+093E C3
    return c1 + "\u0947" + c2 + "\u093E" + c3


def _redistribute_double_matra(match: re.Match) -> str:
    """Split double matra across two consonants."""
    c1, c2, m1, m2 = match.group(1), match.group(2), match.group(3), match.group(4)
    # C1 C2 M1 M2 -> C1+M1 C2+M2
    return c1 + m1 + c2 + m2


def _fix_halant_issues(text: str) -> str:
    """Fix halant-related issues."""
    # Remove halant at word boundaries (before space)
    text = re.sub(r"\u094D(\s)", r"\1", text)
    # Remove halant at end of text
    text = re.sub(r"\u094D$", "", text)
    # Remove multiple halants
    text = re.sub(r"\u094D{2,}", "\u094D", text)
    return text


def validate_and_repair_word(word: str) -> tuple[str, float, str]:
    """
    Validate and repair a single Devanagari word using the Nepal Knowledge
    Base (app.intelligence.nepal_knowledge_base) for word-level correction.

    This is a required internal dependency, not an optional one: an earlier
    version of this function silently fell back to a local `_COMMON_WORDS`
    set if the import failed, but that set had been corrupted (mojibake)
    and could never match anything, so the fallback path was already
    non-functional. Failing loudly here is preferable to silently
    degrading validation quality.
    """
    if not word or not _DEVANAGARI_RE.search(word):
        return word, 100.0, ""

    from app.intelligence.nepal_knowledge_base import is_known_word, correct_word

    if is_known_word(word):
        return word, 100.0, ""

    # Validate Unicode sequences
    result = validate_devanagari_text(word)
    if result.is_valid:
        return word, 95.0, ""

    # Try knowledge base correction
    corrected, conf, source = correct_word(word)
    if corrected != word and conf >= 65:
        return corrected, conf, f"kb_{source}"

    # Try repair
    repaired = result.repaired_text
    if is_known_word(repaired):
        return repaired, 90.0, "repaired_to_known_word"

    if repaired != word:
        return repaired, 70.0, "sequence_repaired"

    return word, 60.0, "unvalidated"



def validate_text_block(text: str) -> dict[str, Any]:
    """
    Validate an entire text block and return quality metrics.
    """
    if not text:
        return {"valid": True, "confidence": 0, "word_count": 0}

    words = re.findall(r"[\u0900-\u097F]+", text)
    if not words:
        return {"valid": True, "confidence": 100, "word_count": 0}

    total_confidence = 0.0
    repairs = 0
    repaired_words = []

    for word in words:
        repaired, conf, reason = validate_and_repair_word(word)
        total_confidence += conf
        if reason:
            repairs += 1
        repaired_words.append(repaired)

    from app.intelligence.nepal_knowledge_base import is_known_word

    avg_confidence = total_confidence / len(words)
    known_ratio = sum(1 for w in repaired_words if is_known_word(w)) / len(words)

    return {
        "valid": avg_confidence >= 70,
        "confidence": round(avg_confidence, 1),
        "word_count": len(words),
        "known_word_ratio": round(known_ratio * 100, 1),
        "repairs": repairs,
        "repair_ratio": round(repairs / len(words) * 100, 1),
    }

