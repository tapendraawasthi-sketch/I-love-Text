"""
Multi-engine text extraction and validation.

For critical pages, extract text using multiple methods and compare:
1. PyMuPDF direct text extraction
2. PyMuPDF "blocks" extraction with layout reconstruction
3. OCR (if text layer quality is low)

The engine that produces the best result (by scoring) wins.
This alone can dramatically improve quality.
"""
from __future__ import annotations

import re
from typing import Any

import fitz

from app.extract.document_model import BBox, DocumentElement, ElementType
from app.logging_config import get_logger

logger = get_logger("MultiEngineValidator")

_DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]")
_GARBAGE_RE = re.compile(r"undefined|NaN|\[object", re.I)


def _score_text(text: str) -> float:
    """Score text quality. Higher = better."""
    if not text or not text.strip():
        return 0.0

    chars = [c for c in text if c.strip()]
    if not chars:
        return 0.0

    score = 0.0

    # Devanagari content
    deva = sum(1 for c in chars if _DEVANAGARI_RE.match(c))
    deva_ratio = deva / len(chars)
    score += deva_ratio * 40

    # Text length (more text = probably better)
    score += min(30, len(text.strip()) / 50)

    # Word count
    words = text.split()
    score += min(15, len(words) / 10)

    # Penalties
    if _GARBAGE_RE.search(text):
        score -= 20

    # PUA characters
    pua = sum(1 for c in text if "\uE000" <= c <= "\uF8FF")
    score -= min(10, pua * 2)

    # Excessive repetition
    if len(words) > 10:
        unique = len(set(words))
        if unique / len(words) < 0.3:
            score -= 15

    return max(0, score)


def extract_text_method(page: fitz.Page) -> dict[str, str]:
    """
    Extract text from a page using multiple methods.
    
    Returns dict mapping method name to extracted text.
    """
    results = {}

    # Method 1: Simple text extraction with sort
    try:
        text1 = page.get_text("text", sort=True)
        results["text_sorted"] = text1.strip()
    except Exception:
        results["text_sorted"] = ""

    # Method 2: Block-based extraction
    try:
        blocks = page.get_text("blocks", sort=True)
        block_texts = []
        for b in blocks:
            if b[6] == 0:  # Text block
                block_texts.append(b[4].strip())
        results["blocks_sorted"] = "\n\n".join(block_texts)
    except Exception:
        results["blocks_sorted"] = ""

    # Method 3: Dict extraction with span-level detail
    try:
        page_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
        lines = []
        for block in page_dict.get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                line_text = " ".join(
                    span.get("text", "")
                    for span in line.get("spans", [])
                    if span.get("text", "").strip()
                )
                if line_text.strip():
                    lines.append(line_text.strip())
        results["dict_spans"] = "\n".join(lines)
    except Exception:
        results["dict_spans"] = ""

    return results


def select_best_extraction(
    methods: dict[str, str],
    prefer_devanagari: bool = True,
) -> tuple[str, str]:
    """
    Compare multiple extraction results and return the best one.
    
    Returns: (best_text, method_name)
    """
    if not methods:
        return "", "none"

    scored = []
    for method_name, text in methods.items():
        score = _score_text(text)
        if prefer_devanagari:
            chars = [c for c in text if c.strip()]
            if chars:
                deva = sum(1 for c in chars if _DEVANAGARI_RE.match(c))
                if deva / len(chars) > 0.3:
                    score *= 1.15  # Bonus for high Devanagari content
        scored.append((score, method_name, text))

    scored.sort(key=lambda x: x[0], reverse=True)

    best_score, best_method, best_text = scored[0]

    # Cross-validate: if top two methods agree, higher confidence
    if len(scored) >= 2:
        second_score, _, second_text = scored[1]
        # Simple similarity check
        if _texts_similar(best_text, second_text):
            logger.debug("Top 2 methods agree (score %.1f vs %.1f)", best_score, second_score)
        else:
            logger.debug(
                "Methods disagree: %s (%.1f) vs %s (%.1f)",
                scored[0][1], scored[0][0],
                scored[1][1], scored[1][0],
            )

    return best_text, best_method


def _texts_similar(text1: str, text2: str, threshold: float = 0.8) -> bool:
    """Check if two texts are similar enough to confirm each other."""
    if not text1 or not text2:
        return False

    # Compare word sets
    words1 = set(text1.split())
    words2 = set(text2.split())

    if not words1 or not words2:
        return False

    overlap = len(words1 & words2)
    total = max(len(words1), len(words2))

    return overlap / total >= threshold


def validate_page_extraction(
    page: fitz.Page,
    primary_text: str,
    primary_method: str,
) -> dict[str, Any]:
    """
    Validate a page's extraction by comparing with alternative methods.
    
    Returns validation report.
    """
    methods = extract_text_method(page)
    methods[primary_method] = primary_text

    best_text, best_method = select_best_extraction(methods)

    primary_score = _score_text(primary_text)
    best_score = _score_text(best_text)

    agreement_count = sum(
        1 for m, t in methods.items()
        if _texts_similar(t, best_text)
    )

    return {
        "primary_method": primary_method,
        "primary_score": round(primary_score, 1),
        "best_method": best_method,
        "best_score": round(best_score, 1),
        "methods_agree": agreement_count,
        "total_methods": len(methods),
        "should_use_alternative": (
            best_method != primary_method and
            best_score > primary_score * 1.15
        ),
        "alternative_text": best_text if best_method != primary_method else None,
    }
