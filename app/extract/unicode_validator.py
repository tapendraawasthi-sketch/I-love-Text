"""
Unicode Validator and Repair Engine for Devanagari text.

This is the critical component that detects and fixes broken Unicode
sequences ΓÇö the #1 source of errors in Nepali PDF extraction.

Common problems:
    1. Matra reordering: αñ¿αñ¬αÑçαñ╛αñ▓ ΓåÆ αñ¿αÑçαñ¬αñ╛αñ▓  ( αÑç matra placed after wrong consonant)
    2. Split conjuncts: αñ╡αñ┐αñªαÑìαñ»αññαÑü ΓåÆ αñ╡αñ┐αñªαÑìαñ»αÑüαññ  (halant+consonant in wrong order)
    3. Orphan matras: αñ┐ appearing without consonant base
    4. Double matras: αñòαñ╛αñ╛αñ« ΓåÆ αñòαñ╛αñ«
    5. Wrong NFC normalization

The validator works at three levels:
    1. Character level: Is this character valid after the previous one?
    2. Syllable level: Is this syllable a valid Devanagari syllable?
    3. Word level: Is this word a known Nepali word (or close to one)?
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

# Common Nepali words for validation (high-frequency legal/government terms)
_COMMON_WORDS = {
    "αñ¿αÑçαñ¬αñ╛αñ▓", "αñ╕αñ░αñòαñ╛αñ░", "αñÉαñ¿", "αñ¿αñ┐αñ»αñ«", "αñòαñ╛αñ¿αÑéαñ¿", "αñàαñªαñ╛αñ▓αññ", "αñ╡αñ┐αñºαÑçαñ»αñò",
    "αñ╕αñéαñ╡αñ┐αñºαñ╛αñ¿", "αñ¬αÑìαñ░αñªαÑçαñ╢", "αñ£αñ┐αñ▓αÑìαñ▓αñ╛", "αñùαñ╛αñëαñüαñ¬αñ╛αñ▓αñ┐αñòαñ╛", "αñ¿αñùαñ░αñ¬αñ╛αñ▓αñ┐αñòαñ╛",
    "αñ«αñ╣αñ╛αñ¿αñùαñ░αñ¬αñ╛αñ▓αñ┐αñòαñ╛", "αñëαñ¬αñ«αñ╣αñ╛αñ¿αñùαñ░αñ¬αñ╛αñ▓αñ┐αñòαñ╛", "αñòαñ░", "αñåαñ»αñòαñ░", "αñ«αÑéαñ▓αÑìαñ»",
    "αñàαñ¡αñ┐αñ╡αÑâαñªαÑìαñºαñ┐", "αñ╢αÑüαñ▓αÑìαñò", "αñªαñ╕αÑìαññαÑüαñ░", "αñ¿αñ┐αñ░αÑìαñºαñ╛αñ░αñú", "αñ¡αÑüαñòαÑìαññαñ╛αñ¿αÑÇ",
    "αñ╡αñ┐αñªαÑìαñ»αÑüαññ", "αñ╡αñ┐αñªαÑìαñ»αÑüαññαÑÇαñ»", "αñ¬αÑìαñ░αññαñ┐αñ╢αññ", "αñàαñ¿αÑüαñ╕αÑéαñÜαÑÇ", "αñªαñ½αñ╛",
    "αñëαñ¬αñªαñ½αñ╛", "αñûαñúαÑìαñí", "αñ¬αÑìαñ░αñòαñ░αñú", "αñ¡αñ╛αñù", "αñ¬αñ░αñ┐αñÜαÑìαñ¢αÑçαñª",
    "αñ╡αÑìαñ»αñ╡αñ╕αÑìαñÑαñ╛", "αñ¬αÑìαñ░αñ╛αñ╡αñºαñ╛αñ¿", "αñ╕αñéαñ╢αÑïαñºαñ¿", "αñ¿αñ┐αñ░αÑìαñªαÑçαñ╢αñ¿", "αñåαñªαÑçαñ╢",
    "αñàαñºαÑìαñ»αñ╛αñªαÑçαñ╢", "αñ░αñ╛αñ£αñ¬αññαÑìαñ░", "αñ¬αÑìαñ░αñ«αñ╛αñúαÑÇαñòαñ░αñú", "αñòαñ╛αñ░αÑìαñ»αñ╛αñ¿αÑìαñ╡αñ»αñ¿",
    "αñ»αÑïαñ£αñ¿αñ╛", "αñ¼αñ£αÑçαñƒ", "αñ¬αÑìαñ░αññαñ┐αñ╡αÑçαñªαñ¿", "αñ▓αÑçαñûαñ╛αñ¬αñ░αÑÇαñòαÑìαñ╖αñú", "αñ«αñ╣αñ╛αñ▓αÑçαñûαñ╛αñ¬αñ░αÑÇαñòαÑìαñ╖αñò",
    "αñ░αñ╛αñ╖αÑìαñƒαÑìαñ░", "αñ¼αÑêαñéαñò", "αñ╡αñ┐αññαÑìαññαÑÇαñ»", "αñåαñ░αÑìαñÑαñ┐αñò", "αñ╡αñ░αÑìαñ╖", "αñ╕αñ╛αñ▓",
    "αñ«αñ┐αññαñ┐", "αññαñ╛αñ░αñ┐αñû", "αñ¿αñ┐αñ░αÑìαñúαñ»", "αñ¼αÑêαñáαñò", "αñ╕αñ«αñ┐αññαñ┐", "αñàαñºαÑìαñ»αñòαÑìαñ╖",
    "αñ╕αñªαñ╕αÑìαñ»", "αñ╕αñÜαñ┐αñ╡", "αñ«αñ¿αÑìαññαÑìαñ░αñ╛αñ▓αñ»", "αñ╡αñ┐αñ¡αñ╛αñù", "αñòαñ╛αñ░αÑìαñ»αñ╛αñ▓αñ»",
    "αñàαñºαñ┐αñòαñ╛αñ░αÑÇ", "αñòαñ░αÑìαñ«αñÜαñ╛αñ░αÑÇ", "αñ╕αÑçαñ╡αñ╛", "αñ¬αñª", "αññαñ▓αñ¼", "αñ¡αññαÑìαññαñ╛",
    "αñ¿αñ┐αñ╡αÑâαññαÑìαññαñ┐αñ¡αñ░αñú", "αñëαñ¬αñÜαñ╛αñ░", "αñ╕αÑìαñ╡αñ╛αñ╕αÑìαñÑαÑìαñ»", "αñ╢αñ┐αñòαÑìαñ╖αñ╛", "αñ╡αñ┐αñ╢αÑìαñ╡αñ╡αñ┐αñªαÑìαñ»αñ╛αñ▓αñ»",
}

# Build consonant skeleton lookup for fuzzy matching
def _consonant_skeleton(word: str) -> str:
    """Extract consonant skeleton (strip matras, halant, anusvara etc.)."""
    return "".join(
        c for c in word
        if ord(c) in _DEVANAGARI_CONSONANTS
    )

_WORD_SKELETONS: dict[str, list[str]] = {}
for w in _COMMON_WORDS:
    sk = _consonant_skeleton(w)
    _WORD_SKELETONS.setdefault(sk, []).append(w)


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

    # 1. Fix matra reordering: αñ┐ matra (0x093F) issues
    # In many broken PDFs, pre-base matras end up after the wrong consonant
    # Pattern: consonant + wrong_consonant + αñ┐ ΓåÆ consonant + αñ┐ + wrong_consonant is wrong
    # Actually: the real issue is that matras get attached to wrong consonants

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
            # Orphan matra ΓÇö skip it
            logger.debug("Removing orphan matra U+%04X at position %d", cp, i)
            continue
        result.append(char)

    return "".join(result)


def _fix_matra_displacement(text: str) -> str:
    """
    Fix common matra displacement patterns in extracted text.

    The most critical repair: when PDF extraction places matras on wrong consonants.

    Example: αñ¿αñ¬αÑçαñ╛αñ▓ ΓåÆ αñ¿αÑçαñ¬αñ╛αñ▓
    What happened: αÑçαñ╛ (two matras) got placed after αñ¬ instead of after αñ¿ and αñ¬ separately

    Strategy: Use syllable structure analysis to detect impossible combinations
    and redistribute matras to their correct consonant bases.
    """
    # Pattern: consonant + αñ╛ + αÑï  or similar impossible combos
    # αÑçαñ╛ is impossible (two matras on same consonant without halant between)
    # Fix: redistribute

    # Common specific fixes for known broken patterns
    fixes = [
        # Pattern: C + αÑç + αñ╛ ΓåÆ should check if this makes sense
        # αñ¿αñ¬αÑçαñ╛αñ▓ ΓåÆ αñ¿αÑçαñ¬αñ╛αñ▓: the αÑçαñ╛ after αñ¬ is wrong, should be αñ¿αÑç + αñ¬αñ╛ + αñ▓
        (r"([\u0915-\u0939])([\u0915-\u0939])\u0947\u093E([\u0915-\u0939])",
         _redistribute_matras_3),

        # Double matra that should be split across consonants
        # pattern: C1 C2 + matra1 + matra2 ΓåÆ C1+matra1 C2+matra2
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
    # C1 C2 αÑç αñ╛ C3 ΓåÆ C1 αÑç C2 αñ╛ C3
    return c1 + "\u0947" + c2 + "\u093E" + c3


def _redistribute_double_matra(match: re.Match) -> str:
    """Split double matra across two consonants."""
    c1, c2, m1, m2 = match.group(1), match.group(2), match.group(3), match.group(4)
    # C1 C2 M1 M2 ΓåÆ C1+M1 C2+M2
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


# In the validate_and_repair_word function, replace the hardcoded _COMMON_WORDS
# lookup with the knowledge base:

def validate_and_repair_word(word: str) -> tuple[str, float, str]:
    """
    Validate and repair a single Devanagari word.
    Now uses the Nepal Knowledge Base for correction.
    """
    if not word or not _DEVANAGARI_RE.search(word):
        return word, 100.0, ""

    # Check against knowledge base (much larger vocabulary)
    try:
        from app.intelligence.nepal_knowledge_base import (
            is_known_word, correct_word,
        )

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

    except ImportError:
        # Fallback if knowledge base not available
        if word in _COMMON_WORDS:
            return word, 100.0, ""

        result = validate_devanagari_text(word)
        if result.is_valid:
            return word, 95.0, ""

        repaired = result.repaired_text
        if repaired in _COMMON_WORDS:
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

    avg_confidence = total_confidence / len(words)
    known_ratio = sum(1 for w in repaired_words if w in _COMMON_WORDS) / len(words)

    return {
        "valid": avg_confidence >= 70,
        "confidence": round(avg_confidence, 1),
        "word_count": len(words),
        "known_word_ratio": round(known_ratio * 100, 1),
        "repairs": repairs,
        "repair_ratio": round(repairs / len(words) * 100, 1),
    }

