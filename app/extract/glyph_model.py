"""
Glyph-level document object model.

Every character on every page is represented as a GlyphObject — not just a
Unicode codepoint but a full record of position, font, origin, confidence,
and validated Unicode. This is the atomic unit of the Document Reconstruction
Engine.

Architecture:
    PDF → Glyph Extraction → Glyph Validation → Unicode Repair → Word Assembly
    → Paragraph Assembly → Document AST → Serialization
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class GlyphConfidence(Enum):
    """How confident we are in the Unicode mapping for this glyph."""
    VERIFIED = "verified"           # Multiple engines agree
    TRUSTED = "trusted"             # Single engine, valid Unicode
    SUSPICIOUS = "suspicious"       # Valid but possibly wrong ordering
    INVALID = "invalid"             # Known-bad Unicode sequence
    UNKNOWN = "unknown"             # No Unicode mapping available
    REPAIRED = "repaired"           # Was invalid, now fixed


@dataclass
class GlyphObject:
    """
    Atomic unit of document reconstruction.

    Every character extracted from the PDF becomes a GlyphObject.
    This carries enough information to validate, repair, and reconstruct
    the document at character level.
    """
    # Identity
    unicode_char: str               # The Unicode character (may be wrong)
    glyph_id: int = 0              # PDF internal glyph ID
    raw_char: str = ""             # Original character before any conversion

    # Position
    x0: float = 0.0
    y0: float = 0.0
    x1: float = 0.0
    y1: float = 0.0
    origin_x: float = 0.0         # Baseline origin X
    origin_y: float = 0.0         # Baseline origin Y

    # Font
    font_name: str = ""
    font_size: float = 0.0
    is_bold: bool = False
    is_italic: bool = False
    color: int = 0

    # Confidence
    confidence: GlyphConfidence = GlyphConfidence.UNKNOWN
    unicode_confidence: float = 0.0  # 0-100
    extraction_source: str = ""      # "pymupdf_dict", "pymupdf_rawdict", etc.

    # Validation
    is_devanagari: bool = False
    is_matra: bool = False
    is_consonant: bool = False
    is_vowel: bool = False
    is_halant: bool = False
    is_valid_sequence: bool = True   # Whether this char is valid after prev char

    # Repair
    repaired_char: str = ""          # If repaired, the corrected character
    repair_reason: str = ""

    @property
    def final_char(self) -> str:
        """Return the best available character."""
        if self.repaired_char:
            return self.repaired_char
        return self.unicode_char

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def center_x(self) -> float:
        return (self.x0 + self.x1) / 2.0

    @property
    def center_y(self) -> float:
        return (self.y0 + self.y1) / 2.0


@dataclass
class WordObject:
    """A word assembled from sequential glyphs on the same line."""
    glyphs: list[GlyphObject] = field(default_factory=list)
    x0: float = 0.0
    y0: float = 0.0
    x1: float = 0.0
    y1: float = 0.0
    confidence: float = 0.0
    is_valid_nepali: bool = True

    @property
    def text(self) -> str:
        return "".join(g.final_char for g in self.glyphs)

    @property
    def raw_text(self) -> str:
        return "".join(g.unicode_char for g in self.glyphs)

    def compute_bbox(self) -> None:
        if not self.glyphs:
            return
        self.x0 = min(g.x0 for g in self.glyphs)
        self.y0 = min(g.y0 for g in self.glyphs)
        self.x1 = max(g.x1 for g in self.glyphs)
        self.y1 = max(g.y1 for g in self.glyphs)
        confs = [g.unicode_confidence for g in self.glyphs if g.unicode_confidence > 0]
        self.confidence = sum(confs) / len(confs) if confs else 0.0


@dataclass
class SentenceObject:
    """A sentence assembled from words, respecting Nepali grammar."""
    words: list[WordObject] = field(default_factory=list)
    is_complete: bool = True      # Does sentence end with purna viram?
    continues_next_line: bool = False  # Justified text wrapping

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)


@dataclass
class ParagraphObject:
    """A paragraph — the key unit between heading and table."""
    sentences: list[SentenceObject] = field(default_factory=list)
    x0: float = 0.0
    y0: float = 0.0
    x1: float = 0.0
    y1: float = 0.0
    font_size: float = 0.0
    is_bold: bool = False
    indent: float = 0.0           # Left indent relative to margin

    @property
    def text(self) -> str:
        return " ".join(s.text for s in self.sentences)


# --- Devanagari Unicode classification ---

# Unicode ranges for Devanagari
_DEVANAGARI_CONSONANTS = set(range(0x0915, 0x093A))  # क to ह
_DEVANAGARI_VOWELS = set(range(0x0904, 0x0915))      # अ to औ
_DEVANAGARI_MATRAS = set(range(0x093E, 0x094D))       # ा to ्  (excluding halant)
_DEVANAGARI_HALANT = 0x094D                            # ्
_DEVANAGARI_ANUSVARA = 0x0902                          # ं
_DEVANAGARI_CHANDRABINDU = 0x0901                      # ँ
_DEVANAGARI_VISARGA = 0x0903                           # ः
_DEVANAGARI_NUKTA = 0x093C                             # ़
_DEVANAGARI_DIGITS = set(range(0x0966, 0x0970))        # ० to ९
_DEVANAGARI_DANDA = 0x0964                             # ।
_DEVANAGARI_DOUBLE_DANDA = 0x0965                      # ॥

# Pre-consonant matras (ि) — must come BEFORE consonant visually
# but AFTER consonant in Unicode. This is the #1 source of errors.
_PREBASE_MATRAS = {0x093F}  # ि


def classify_devanagari_char(char: str) -> dict[str, bool]:
    """Classify a single character's Devanagari properties."""
    if not char:
        return {}
    cp = ord(char)
    return {
        "is_devanagari": 0x0900 <= cp <= 0x097F,
        "is_consonant": cp in _DEVANAGARI_CONSONANTS,
        "is_vowel": cp in _DEVANAGARI_VOWELS,
        "is_matra": cp in _DEVANAGARI_MATRAS or cp == _DEVANAGARI_HALANT,
        "is_halant": cp == _DEVANAGARI_HALANT,
        "is_anusvara": cp == _DEVANAGARI_ANUSVARA,
        "is_chandrabindu": cp == _DEVANAGARI_CHANDRABINDU,
        "is_visarga": cp == _DEVANAGARI_VISARGA,
        "is_nukta": cp == _DEVANAGARI_NUKTA,
        "is_digit": cp in _DEVANAGARI_DIGITS,
        "is_prebase_matra": cp in _PREBASE_MATRAS,
    }


def is_valid_devanagari_sequence(prev_char: str, curr_char: str) -> bool:
    """
    Check if curr_char is valid after prev_char in Devanagari Unicode.

    Invalid sequences include:
    - Matra without preceding consonant
    - Double matras of same type
    - Halant at word start
    - Vowel sign after independent vowel
    """
    if not prev_char or not curr_char:
        return True

    prev = classify_devanagari_char(prev_char)
    curr = classify_devanagari_char(curr_char)

    if not curr.get("is_devanagari") or not prev.get("is_devanagari"):
        return True  # Non-Devanagari sequences are always valid

    # Matra must follow consonant, halant+consonant, or nukta
    if curr.get("is_matra") and not curr.get("is_halant"):
        if not (prev.get("is_consonant") or prev.get("is_nukta") or
                prev.get("is_halant") or prev.get("is_matra")):
            return False

    # Halant must follow consonant or nukta
    if curr.get("is_halant"):
        if not (prev.get("is_consonant") or prev.get("is_nukta")):
            return False

    # Independent vowel cannot be followed by a matra (except anusvara/chandrabindu)
    if prev.get("is_vowel") and curr.get("is_matra"):
        if not (curr.get("is_anusvara") or curr.get("is_chandrabindu")):
            return False

    # Double same matra is invalid
    if (curr.get("is_matra") and prev.get("is_matra") and
            ord(prev_char) == ord(curr_char)):
        return False

    return True
