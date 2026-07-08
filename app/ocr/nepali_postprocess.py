"""
Post-processing for Nepali/Devanagari OCR output.
"""
from __future__ import annotations

import re
import unicodedata

from app.nlp.nepali_sentence_intelligence import (
    corruption_score,
    repair_corrupted_devanagari,
)

_ZERO_WIDTH_RE = re.compile(r"[\u200b\u200c\u200d\ufeff]")
_MULTI_SPACE_RE = re.compile(r"[^\S\n]+")
_DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]")


def normalize_nepali_text(text: str) -> str:
    """Normalize OCR output for cleaner Nepali Unicode and layout."""
    if not text:
        return ""

    text = unicodedata.normalize("NFC", text)
    if _DEVANAGARI_RE.search(text) and corruption_score(text) > 0:
        text = repair_corrupted_devanagari(text)
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
