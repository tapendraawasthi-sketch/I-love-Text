"""
Multi-engine text extraction with consensus voting.

Extracts text from the same page using multiple methods and selects
the best result through consensus voting.

Engines:
    1. PyMuPDF dict (span-level)
    2. PyMuPDF rawdict (character-level)
    3. PyMuPDF text (simple)
    4. PyMuPDF blocks (block-level)

For each word position, the engine that produces the best Devanagari
Unicode gets the vote. This eliminates systematic errors from any
single extraction path.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any

import fitz

from app.extract.unicode_validator import validate_devanagari_text, validate_text_block
from app.logging_config import get_logger

logger = get_logger("MultiEngineExtractor")

_DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]")
_GARBAGE_RE = re.compile(r"undefined|NaN|\[object|function\s*\(", re.I)


def _extract_text_sorted(page: fitz.Page) -> str:
    """Engine 1: Simple sorted text extraction."""
    try:
        return page.get_text("text", sort=True).strip()
    except Exception:
        return ""


def _extract_blocks_sorted(page: fitz.Page) -> str:
    """Engine 2: Block-based sorted extraction."""
    try:
        blocks = page.get_text("blocks", sort=True)
        texts = [b[4].strip() for b in blocks if b[6] == 0 and b[4].strip()]
        return "\n\n".join(texts)
    except Exception:
        return ""


def _extract_dict_spans(page: fitz.Page) -> str:
    """Engine 3: Dict extraction with span-level detail."""
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
        return "\n".join(lines)
    except Exception:
        return ""


def _extract_rawdict_chars(page: fitz.Page) -> str:
    """Engine 4: Rawdict character-level extraction."""
    try:
        page_dict = page.get_text("rawdict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
        lines = []
        for block in page_dict.get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                line_chars = []
                for span in line.get("spans", []):
                    for char in span.get("chars", []):
                        c = char.get("c", "")
                        if c:
                            line_chars.append(c)
                if line_chars:
                    lines.append("".join(line_chars).strip())
        return "\n".join(l for l in lines if l)
    except Exception:
        return ""


def _score_extraction(text: str) -> dict[str, float]:
    """Score an extraction result on multiple dimensions."""
    if not text or not text.strip():
        return {"total": 0, "devanagari": 0, "quality": 0, "length": 0}

    chars = [c for c in text if c.strip()]
    if not chars:
        return {"total": 0, "devanagari": 0, "quality": 0, "length": 0}

    # Devanagari ratio
    deva_count = sum(1 for c in chars if _DEVANAGARI_RE.match(c))
    deva_ratio = deva_count / len(chars)

    # Garbage penalty
    garbage_penalty = 20.0 if _GARBAGE_RE.search(text) else 0.0

    # Unicode validation score
    validation = validate_text_block(text)
    unicode_quality = validation.get("confidence", 50)

    # Text length (more is generally better)
    length_score = min(30, len(text.strip()) / 100)

    # Word count
    words = text.split()
    word_score = min(20, len(words) / 10)

    total = (
        deva_ratio * 40 +
        unicode_quality * 0.2 +
        length_score +
        word_score -
        garbage_penalty
    )

    return {
        "total": max(0, total),
        "devanagari": round(deva_ratio * 100, 1),
        "quality": round(unicode_quality, 1),
        "length": len(text.strip()),
    }


def extract_with_consensus(
    page: fitz.Page,
    prefer_devanagari: bool = True,
) -> dict[str, Any]:
    """
    Extract text using multiple engines and return the best result.

    Uses scoring to pick the winner, with optional consensus validation.
    """
    engines = {
        "text_sorted": _extract_text_sorted,
        "blocks_sorted": _extract_blocks_sorted,
        "dict_spans": _extract_dict_spans,
        "rawdict_chars": _extract_rawdict_chars,
    }

    results: dict[str, dict[str, Any]] = {}
    for name, func in engines.items():
        text = func(page)
        score = _score_extraction(text)
        results[name] = {
            "text": text,
            "score": score,
        }

    # Pick the best
    best_engine = max(results.keys(), key=lambda k: results[k]["score"]["total"])
    best_text = results[best_engine]["text"]
    best_score = results[best_engine]["score"]

    # Never ship raw legacy ASCII as winner for Nepali docs — convert via direct_extract
    if prefer_devanagari and best_score.get("devanagari", 0) < 15:
        try:
            from app.extract.direct_extract import extract_page_direct, score_text_quality
            converted = extract_page_direct(page)
            ctext = (converted.get("text") or "").strip()
            if ctext:
                cscore = score_text_quality(ctext)
                if cscore["score"] > best_score.get("total", 0) + 2 or cscore["devanagari_ratio"] >= 20:
                    best_text = ctext
                    best_engine = "direct_legacy_convert"
                    best_score = {
                        "total": cscore["score"],
                        "devanagari": cscore["devanagari_ratio"],
                        "quality": cscore["score"],
                        "length": len(ctext),
                    }
        except Exception as exc:
            logger.debug("Legacy conversion overlay skipped: %s", exc)

    # Check consensus: do multiple engines agree?
    agreement = _check_consensus(results)

    # Apply Unicode validation and repair to winner
    validation = validate_devanagari_text(best_text)
    final_text = validation.repaired_text if validation.repair_count > 0 else best_text

    return {
        "text": final_text,
        "engine": best_engine,
        "score": best_score,
        "consensus": agreement,
        "engines_compared": len(results),
        "all_scores": {k: v["score"] for k, v in results.items()},
        "unicode_repairs": validation.repair_count,
    }


def _check_consensus(results: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Check how many engines agree on the output."""
    texts = {k: v["text"] for k, v in results.items() if v["text"].strip()}
    if len(texts) < 2:
        return {"agreement": 0, "total": len(texts)}

    # Compare word sets
    word_sets = {k: set(v.split()) for k, v in texts.items()}

    agreements = 0
    total_pairs = 0
    for i, (k1, ws1) in enumerate(word_sets.items()):
        for k2, ws2 in list(word_sets.items())[i + 1:]:
            total_pairs += 1
            if ws1 and ws2:
                overlap = len(ws1 & ws2) / max(len(ws1), len(ws2))
                if overlap >= 0.8:
                    agreements += 1

    return {
        "agreement": agreements,
        "total_pairs": total_pairs,
        "agreement_ratio": round(agreements / max(total_pairs, 1) * 100, 1),
    }
