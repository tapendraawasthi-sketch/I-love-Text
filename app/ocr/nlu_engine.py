"""NLU Engine from My-Current-ERP - Nepali language understanding for OCR."
""Adapted for OCR context (text validation, not accounting).
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from app.config import FAST_MODEL, OLLAMA_BASE_URL

try:
    from ollama import Client
except ImportError:
    Client = None

logger = logging.getLogger(__name__)


def detect_language_from_text(text: str) -> str:
    """
    Detect primary language in OCR text (Nepali, English, or mixed).
    
    Returns: 'nepali', 'english', or 'mixed'
    """
    devanagari_count = len(re.findall(r"[\u0900-\u097F]", text))
    english_count = len(re.findall(r"[a-zA-Z]", text))
    total = devanagari_count + english_count

    if total == 0:
        return "unknown"
    
    devanagari_ratio = devanagari_count / total
    
    if devanagari_ratio > 0.7:
        return "nepali"
    elif devanagari_ratio < 0.3:
        return "english"
    else:
        return "mixed"


def validate_nepali_text(text: str) -> dict:
    """
    Validate OCR-extracted Nepali text for common OCR errors.
    
    Returns:
    {
        "is_valid": bool,
        "issues": [list of detected problems],
        "severity": "low", "medium", "high",
        "confidence_adjustment": -0.15 to +0.1,
    }
    """
    issues = []
    confidence_adjustment = 0.0
    
    # Check for orphaned diacritics (common OCR error)
    orphaned_diacritics = re.findall(r"[\u0941-\u0948](?![\u0900-\u0940\u0949-\u097F])", text)
    if orphaned_diacritics:
        issues.append(f"Orphaned diacritics detected: {len(orphaned_diacritics)} instances")
        confidence_adjustment -= 0.1
    
    # Check for mixed script within words (likely error)
    mixed_words = re.findall(r"[\u0900-\u097F][a-zA-Z]|[a-zA-Z][\u0900-\u097F]", text)
    if mixed_words and len(text.split()) > 5:
        issues.append(f"Script mixing within words: {len(mixed_words)} instances")
        confidence_adjustment -= 0.05
    
    # Check for common OCR confusions
    if re.search(r"[l1|I](?![a-z])", text):  # Possible confusion: l, 1, |, I
        issues.append("Possible OCR confusion: l/1/|/I")
        confidence_adjustment -= 0.05
    
    severity = "high" if confidence_adjustment < -0.1 else ("medium" if confidence_adjustment < -0.05 else "low")
    
    return {
        "is_valid": len(issues) == 0,
        "issues": issues,
        "severity": severity,
        "confidence_adjustment": confidence_adjustment,
    }


def resolve_ocr_ambiguities(text: str, context: dict | None = None) -> str:
    """
    Use context to resolve ambiguous OCR interpretations.
    
    Args:
        text: OCR text with potential ambiguities
        context: Optional context dict with previous_text, domain, etc.
    
    Returns:
        Potentially corrected text
    """
    # Common OCR confusions in Nepali/English
    replacements = {
        r"(?<![\u0900-\u097F])[l1|](?![a-z])": "I",  # 1/l/| → I in English
        r"0(?=[A-Z])": "O",  # 0 at start of words → O
        r"(?<=\s)l(?=\s|$)": "I",  # Single l → I
    }
    
    corrected = text
    for pattern, replacement in replacements.items():
        corrected = re.sub(pattern, replacement, corrected)
    
    return corrected
