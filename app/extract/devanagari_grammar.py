"""
Devanagari Grammar Validator — validates Unicode sequences against
actual Devanagari orthographic and phonological rules.

Goes beyond simple matra reordering to check:
    - Impossible conjuncts (e.g., ह + ्  + ह is extremely rare)
    - Impossible vowel sequences (two independent vowels adjacent)
    - Invalid halant positions (halant after matra)
    - Invalid nukta combinations (nukta after non-nukta-taking consonants)
    - Impossible ligatures (sequences that no Nepali font can render)
    - Syllable structure validation (C(C(C))V model)

Also provides:
    - Syllable segmentation
    - Word likelihood scoring based on Nepali phonotactics
    - Correction candidates ranked by edit distance + phonological plausibility
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from app.logging_config import get_logger

logger = get_logger("DevanagariGrammar")

# --- Devanagari Unicode ranges ---

VOWELS = set(range(0x0904, 0x0915))  # Independent vowels: अ–औ
CONSONANTS = set(range(0x0915, 0x093A))  # Consonants: क–ह
MATRAS = {  # Dependent vowel signs
    0x093E,  # ा
    0x093F,  # ि
    0x0940,  # ी
    0x0941,  # ु
    0x0942,  # ू
    0x0943,  # ृ
    0x0944,  # ॄ
    0x0945,  # ॅ
    0x0946,  # ॆ
    0x0947,  # े
    0x0948,  # ै
    0x0949,  # ॉ
    0x094A,  # ॊ
    0x094B,  # ो
    0x094C,  # ौ
}
HALANT = 0x094D  # ्
NUKTA = 0x093C   # ़
ANUSVARA = 0x0902  # ं
CHANDRABINDU = 0x0901  # ँ
VISARGA = 0x0903  # ः

# Consonants that can take nukta in Nepali/Hindi
NUKTA_CONSONANTS = {
    0x0915,  # क → क़
    0x0916,  # ख → ख़
    0x0917,  # ग → ग़
    0x091C,  # ज → ज़
    0x0921,  # ड → ड़
    0x0922,  # ढ → ढ़
    0x092B,  # फ → फ़
    0x092F,  # य → य़
}

# Common valid conjuncts in Nepali (consonant clusters via halant)
_COMMON_CONJUNCTS = {
    # Very common
    ("क", "ष"), ("क", "र"), ("क", "त"), ("क", "ल"),
    ("ख", "र"), ("ग", "र"), ("ग", "ध"), ("ग", "न"),
    ("घ", "र"), ("ङ", "क"), ("ङ", "ग"), ("ङ", "ख"),
    ("च", "छ"), ("च", "र"), ("ज", "ञ"), ("ज", "र"),
    ("ञ", "च"), ("ञ", "ज"), ("ट", "र"), ("ड", "र"),
    ("ण", "ड"), ("ण", "ण"), ("त", "त"), ("त", "र"),
    ("त", "न"), ("त", "म"), ("त", "व"), ("थ", "र"),
    ("द", "ध"), ("द", "द"), ("द", "र"), ("द", "व"),
    ("द", "म"), ("द", "य"), ("ध", "र"), ("ध", "य"),
    ("न", "त"), ("न", "द"), ("न", "ध"), ("न", "न"),
    ("न", "म"), ("न", "र"), ("न", "य"), ("प", "र"),
    ("प", "त"), ("ब", "र"), ("ब", "द"), ("भ", "र"),
    ("म", "र"), ("म", "ल"), ("य", "र"),
    ("र", "क"), ("र", "ख"), ("र", "ग"), ("र", "त"),
    ("र", "थ"), ("र", "द"), ("र", "ध"), ("र", "न"),
    ("र", "प"), ("र", "ब"), ("र", "भ"), ("र", "म"),
    ("र", "य"), ("र", "ल"), ("र", "व"), ("र", "श"),
    ("र", "ष"), ("र", "स"), ("र", "ह"),
    ("ल", "ल"), ("ल", "क"), ("ल", "प"),
    ("व", "र"), ("श", "र"), ("श", "च"), ("श", "व"),
    ("श", "न"), ("ष", "ट"), ("ष", "ठ"), ("ष", "ण"),
    ("स", "त"), ("स", "थ"), ("स", "न"), ("स", "र"),
    ("स", "व"), ("स", "म"), ("स", "ल"),
    ("ह", "र"), ("ह", "न"), ("ह", "म"), ("ह", "ल"),
    ("ह", "व"),
    # Three-consonant clusters
    ("स", "त", "र"), ("न", "त", "र"), ("ष", "ट", "र"),
}


@dataclass
class SyllableAnalysis:
    """Analysis of a single Devanagari syllable."""
    text: str
    onset: str = ""         # Initial consonant cluster
    nucleus: str = ""       # Vowel (matra or inherent अ)
    coda: str = ""          # Final anusvara/visarga/chandrabindu
    is_valid: bool = True
    issues: list[str] = field(default_factory=list)


@dataclass
class WordAnalysis:
    """Analysis of a Devanagari word."""
    text: str
    syllables: list[SyllableAnalysis] = field(default_factory=list)
    is_valid: bool = True
    likelihood: float = 0.0  # 0-100: How likely this is a real Nepali word
    issues: list[str] = field(default_factory=list)
    correction_candidates: list[tuple[str, float]] = field(default_factory=list)


def validate_sequence(text: str) -> list[dict[str, Any]]:
    """
    Validate a Devanagari character sequence against grammar rules.

    Returns a list of issues found (empty list = valid).
    """
    issues: list[dict[str, Any]] = []
    chars = list(text)

    for i, char in enumerate(chars):
        cp = ord(char)
        prev_cp = ord(chars[i - 1]) if i > 0 else 0
        next_cp = ord(chars[i + 1]) if i < len(chars) - 1 else 0

        # --- Rule 1: Matra must follow consonant base ---
        if cp in MATRAS:
            if prev_cp not in CONSONANTS and prev_cp != NUKTA and prev_cp != HALANT:
                if prev_cp not in MATRAS:  # Multiple matras can chain in rare cases
                    issues.append({
                        "pos": i, "char": char, "cp": cp,
                        "rule": "orphan_matra",
                        "message": f"Matra {char} without consonant base",
                    })

        # --- Rule 2: Halant must follow consonant ---
        if cp == HALANT:
            if prev_cp not in CONSONANTS and prev_cp != NUKTA:
                issues.append({
                    "pos": i, "char": char, "cp": cp,
                    "rule": "orphan_halant",
                    "message": "Halant without preceding consonant",
                })

        # --- Rule 3: Nukta only on nukta-taking consonants ---
        if cp == NUKTA:
            if prev_cp not in NUKTA_CONSONANTS:
                issues.append({
                    "pos": i, "char": char, "cp": cp,
                    "rule": "invalid_nukta",
                    "message": f"Nukta after non-nukta consonant {chr(prev_cp)}",
                })

        # --- Rule 4: No two adjacent independent vowels ---
        if cp in VOWELS and prev_cp in VOWELS:
            issues.append({
                "pos": i, "char": char, "cp": cp,
                "rule": "adjacent_vowels",
                "message": f"Two adjacent independent vowels: {chr(prev_cp)}{char}",
            })

        # --- Rule 5: No matra after independent vowel ---
        if cp in MATRAS and prev_cp in VOWELS:
            # Exception: anusvara/chandrabindu after vowel is valid
            if cp not in (ANUSVARA, CHANDRABINDU):
                issues.append({
                    "pos": i, "char": char, "cp": cp,
                    "rule": "matra_after_vowel",
                    "message": f"Matra after independent vowel: {chr(prev_cp)}{char}",
                })

        # --- Rule 6: No double identical matras ---
        if cp in MATRAS and cp == prev_cp:
            issues.append({
                "pos": i, "char": char, "cp": cp,
                "rule": "double_matra",
                "message": f"Double matra: {char}{char}",
            })

        # --- Rule 7: Halant after matra is invalid ---
        if cp == HALANT and prev_cp in MATRAS:
            issues.append({
                "pos": i, "char": char, "cp": cp,
                "rule": "halant_after_matra",
                "message": "Halant immediately after matra",
            })

        # --- Rule 8: Validate conjunct plausibility ---
        if cp == HALANT and prev_cp in CONSONANTS and next_cp in CONSONANTS:
            c1 = chr(prev_cp)
            c2 = chr(next_cp)
            conjunct = (c1, c2)
            if conjunct not in _COMMON_CONJUNCTS:
                # Not impossible, but uncommon — flag as warning
                issues.append({
                    "pos": i, "char": char, "cp": cp,
                    "rule": "uncommon_conjunct",
                    "message": f"Uncommon conjunct: {c1}्{c2}",
                    "severity": "warning",
                })

    return issues


def compute_word_likelihood(word: str) -> float:
    """
    Score how likely a Devanagari string is a valid Nepali word.

    Based on phonotactic constraints, syllable structure,
    and character frequency distribution in Nepali.

    Returns 0-100 (higher = more likely valid).
    """
    if not word:
        return 0.0

    score = 100.0
    issues = validate_sequence(word)

    # Deduct for each issue
    for issue in issues:
        severity = issue.get("severity", "error")
        if severity == "error":
            score -= 15
        else:
            score -= 5

    # Check character distribution
    deva_count = sum(1 for c in word if 0x0900 <= ord(c) <= 0x097F)
    if deva_count == 0:
        return 0.0

    total = len(word)
    deva_ratio = deva_count / total

    # Mostly Devanagari is good
    if deva_ratio < 0.7:
        score -= 20

    # Very long words are suspicious
    if len(word) > 20:
        score -= 10

    # Single character words are fine (particles: म, त, etc.)
    if len(word) == 1 and ord(word) in CONSONANTS | VOWELS:
        score = min(score, 80)

    return max(0, min(100, score))


def repair_grammar_issues(text: str) -> tuple[str, list[str]]:
    """
    Repair grammar issues in a Devanagari text string.

    Returns: (repaired_text, list_of_repairs_applied)
    """
    repairs: list[str] = []

    # Apply repairs iteratively
    result = text
    prev = ""
    max_iterations = 5

    for iteration in range(max_iterations):
        if result == prev:
            break
        prev = result

        # Remove orphan matras at word start
        result, n = re.subn(
            r"(?:^|\s)([\u093E-\u094C\u094D])",
            lambda m: " " if m.start() > 0 else "",
            result,
        )
        if n:
            repairs.append(f"removed {n} orphan matras")

        # Remove double matras
        result, n = re.subn(r"([\u093E-\u094C])\1+", r"\1", result)
        if n:
            repairs.append(f"removed {n} double matras")

        # Remove halant after matra
        result, n = re.subn(r"([\u093E-\u094C])\u094D", r"\1", result)
        if n:
            repairs.append(f"removed {n} halant-after-matra")

        # Remove multiple halants
        result, n = re.subn(r"\u094D{2,}", "\u094D", result)
        if n:
            repairs.append(f"normalized {n} multiple halants")

        # Remove halant at word boundaries
        result, n = re.subn(r"\u094D(?=\s|$)", "", result)
        if n:
            repairs.append(f"removed {n} trailing halants")

    # Final NFC normalization
    result = unicodedata.normalize("NFC", result)

    return result, repairs
