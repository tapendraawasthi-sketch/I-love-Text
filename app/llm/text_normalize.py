"""Unified text normalization for Nepali OCR with LLM integration."""

from __future__ import annotations

import re
import unicodedata

# Roman Nepali spelling variants → canonical form
_SPELLING_VARIANTS: dict[str, str] = {
    "udhaar": "udhar",
    "udhaaro": "udhar",
    "udharo": "udhar",
    "nagad": "cash",
    "nagar": "cash",
    "nakad": "cash",
    "becheko": "becheko",
    "bechyo": "becheko",
    "becha": "becheko",
    "kinyo": "kineko",
    "kinya": "kineko",
    "tireko": "tiryo",
    "tira": "tiryo",
    "diyeko": "diye",
    "deko": "diye",
    "liye": "liyo",
    "liya": "liyo",
    "chha": "cha",
    "chh": "cha",
    "xa": "cha",
    "xaina": "chaina",
    "hunchha": "huncha",
    "hunxa": "huncha",
    "gareko": "gareko",
    "garyo": "gareko",
    "bhada": "bhaada",
    "bhadaa": "bhaada",
    "talab": "salary",
    "kharch": "kharcha",
    "aaja": "aja",
    "hijo": "hijo",
}

_DEVANAGARI_DIGIT = str.maketrans("०१२३४५६७८९", "0123456789")
_DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]")


def normalize_nepali_text(text: str) -> str:
    """Normalize Nepali/English mixed text for LLM processing."""
    if not text:
        return ""
    
    t = unicodedata.normalize("NFKC", text.strip())
    t = t.translate(_DEVANAGARI_DIGIT)
    t = re.sub(r"\s+", " ", t)
    
    # Token-level variant map (preserve Devanagari)
    tokens = re.findall(r"[\u0900-\u097F]+|[a-zA-Z0-9]+", t, re.I)
    out = []
    for tok in tokens:
        low = tok.lower()
        if _DEVANAGARI_RE.search(tok):
            out.append(tok)
        else:
            out.append(_SPELLING_VARIANTS.get(low, tok))
    return " ".join(out)


def extract_confidence_hints(text: str) -> dict:
    """Extract language and script hints from text for confidence assessment."""
    hints = {
        "has_devanagari": bool(_DEVANAGARI_RE.search(text)),
        "has_roman": bool(re.search(r"[a-zA-Z]", text)),
        "has_digits": bool(re.search(r"[0-9०-९]", text)),
        "script_consistency": "pure_devanagari" if bool(_DEVANAGARI_RE.search(text)) and not re.search(r"[a-zA-Z]", text) else "mixed",
    }
    return hints


def detect_devanagari_ratio(text: str) -> float:
    """Calculate ratio of Devanagari characters to total alphanumeric."""
    alphanumeric = re.findall(r"[\u0900-\u097F]|[a-zA-Z0-9]", text)
    if not alphanumeric:
        return 0.0
    devanagari = sum(1 for c in alphanumeric if _DEVANAGARI_RE.match(c))
    return devanagari / len(alphanumeric)
