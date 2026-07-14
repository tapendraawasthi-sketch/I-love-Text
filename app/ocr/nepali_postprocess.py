"""
Post-processing for Nepali/Devanagari OCR output.

Forensic fidelity: NFC + whitespace only — never lexicon swaps.
"""
from __future__ import annotations

import re
import unicodedata

from app.extract.fidelity import (
    allow_lexicon_repair,
    get_fidelity,
    min_corruption_for_repair,
)
from app.nlp.nepali_sentence_intelligence import (
    CORRUPTION_RE,
    corruption_score,
    repair_corrupted_devanagari,
)

_ZERO_WIDTH_RE = re.compile(r"[\u200b\u200c\u200d\ufeff]")
_MULTI_SPACE_RE = re.compile(r"[^\S\n]+")
_DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]")
_ASCII_RE = re.compile(r"[A-Za-z]")
_CURRENCY_RE = re.compile(
    r"(?:Rs\.?|NPR|रु\.?|रू\.?|USD|\$)\s*[\d,\u0966-\u096F]+(?:\.\d+)?",
    re.I,
)
_LEGAL_NUMBER_RE = re.compile(
    r"(?:"
    r"[\u0966-\u096F]+[\.\)]\s*"          # १. २)
    r"|\([\u0915-\u0939\u0966-\u096F]+\)"  # (क) (१)
    r"|[\u0915-\u0939][\.\)]\s*"           # क. ख)
    r"|\d+[\.\)]\s*"                       # 1. 2)
    r"|[a-zA-Z][\.\)]\s*"                  # a. b)
    r"|\([a-zA-Z\d]+\)"                    # (a) (1)
    r")"
)


def normalize_nepali_text(text: str) -> str:
    """Normalize OCR output for cleaner Nepali Unicode and layout."""
    if not text:
        return ""

    text = unicodedata.normalize("NFC", text)

    # Lexicon / context repairs only when fidelity allows and corruption is high.
    # Remote repair_corrupted_devanagari only substitutes words that contain
    # corruption markers — still gated off entirely in forensic mode.
    score = corruption_score(text) if _DEVANAGARI_RE.search(text) else 0.0
    if (
        allow_lexicon_repair()
        and score >= min_corruption_for_repair()
        and _DEVANAGARI_RE.search(text)
    ):
        text = repair_corrupted_devanagari(text)
    elif score > 0 and get_fidelity() in ("balanced", "ocr_max", "forensic"):
        # Strip PUA/replacement glyphs only — no lexicon substitution
        text = CORRUPTION_RE.sub("", text)

    text = _ZERO_WIDTH_RE.sub("", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    lines: list[str] = []
    for line in text.split("\n"):
        if "\t" in line:
            cols = [_MULTI_SPACE_RE.sub(" ", col).strip() for col in line.split("\t")]
            lines.append("\t".join(cols))
        else:
            lines.append(_MULTI_SPACE_RE.sub(" ", line).strip())

    return "\n".join(lines).strip()


def normalize_ocr_content(text: str) -> str:
    """
    Preserve Nepali/English mixed content, numbers, currency, and legal numbering.
    """
    text = normalize_nepali_text(text)
    if not text:
        return text

    # Normalise Devanagari digits spacing around punctuation
    text = re.sub(r"([\u0966-\u096F]+)\s+([\.\)])", r"\1\2", text)

    # Preserve currency tokens — ensure space after symbol
    def _fix_currency(match: re.Match[str]) -> str:
        token = match.group(0)
        return _MULTI_SPACE_RE.sub(" ", token).strip()

    text = _CURRENCY_RE.sub(_fix_currency, text)

    return text


def detect_content_script(text: str) -> str:
    """Classify OCR content: nepali, english, mixed, numeric."""
    if not text.strip():
        return "unknown"
    deva = len(_DEVANAGARI_RE.findall(text))
    ascii_l = len(_ASCII_RE.findall(text))
    digits = sum(1 for c in text if c.isdigit() or "\u0966" <= c <= "\u096F")
    total = deva + ascii_l + digits
    if total == 0:
        return "unknown"
    if deva / total > 0.55:
        return "nepali"
    if ascii_l / total > 0.55:
        return "english"
    if deva > 0 and ascii_l > 0:
        return "mixed"
    if digits / total > 0.5:
        return "numeric"
    return "mixed"
