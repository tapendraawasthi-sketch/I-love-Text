"""
Self-Repair Loop — when a page's extraction confidence is low,
automatically retry with different strategies before accepting the result.

Instead of:
    Extract → Accept

We do:
    Extract → Score → Low? → Retry with Strategy B → Score → Low? →
    Retry with Strategy C → Score → Pick Best

Strategies (in order of preference for digital PDFs):
    1. Font program glyph mapping (highest fidelity)
    2. Multi-engine consensus
    3. Unicode validation + repair
    4. OCR at high DPI (last resort — information loss)

The loop is confidence-driven: it stops as soon as confidence exceeds
the threshold, preserving digital fidelity whenever possible.
"""
from __future__ import annotations

import gc
from dataclasses import dataclass, field
from typing import Any, Callable

import fitz

from app.extract.unicode_validator import validate_text_block
from app.logging_config import get_logger

logger = get_logger("SelfRepairLoop")


@dataclass
class ExtractionAttempt:
    """Record of a single extraction attempt."""
    strategy: str
    text: str
    confidence: float
    unicode_quality: float
    word_count: int
    devanagari_ratio: float
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def composite_score(self) -> float:
        """Overall quality score combining all dimensions."""
        return (
            self.confidence * 0.35 +
            self.unicode_quality * 0.30 +
            min(100, self.word_count / 5) * 0.15 +
            self.devanagari_ratio * 0.20
        )


def _score_text(text: str) -> dict[str, float]:
    """Quick multi-dimensional scoring of extracted text."""
    import re
    _DEVA_RE = re.compile(r"[\u0900-\u097F]")

    if not text or not text.strip():
        return {"confidence": 0, "unicode_quality": 0,
                "word_count": 0, "devanagari_ratio": 0}

    chars = [c for c in text if c.strip()]
    deva = sum(1 for c in chars if _DEVA_RE.match(c))
    deva_ratio = (deva / len(chars) * 100) if chars else 0

    validation = validate_text_block(text)
    unicode_quality = validation.get("confidence", 50)

    words = text.split()
    word_count = len(words)

    return {
        "confidence": unicode_quality,
        "unicode_quality": unicode_quality,
        "word_count": word_count,
        "devanagari_ratio": round(deva_ratio, 1),
    }


def self_repair_page(
    page: fitz.Page,
    page_number: int,
    font_lookup: dict[str, Any],
    font_programs: dict | None = None,
    lang: str = "nep+eng",
    confidence_threshold: float = 75.0,
    max_attempts: int = 4,
) -> ExtractionAttempt:
    """
    Extract text from a page with automatic retry on low confidence.

    Tries strategies in order of digital fidelity (highest first):
        1. Font program glyph reconstruction
        2. Direct text + Unicode validation + repair
        3. Multi-engine consensus
        4. OCR (last resort)

    Returns the best attempt.
    """
    attempts: list[ExtractionAttempt] = []

    # --- Strategy 1: Font program glyph reconstruction ---
    if font_programs and max_attempts >= 1:
        try:
            from app.extract.font_program_parser import resolve_glyph_unicode
            text = _extract_via_font_programs(page, font_programs)
            scores = _score_text(text)
            attempt = ExtractionAttempt(
                strategy="font_program",
                text=text,
                **scores,
            )
            attempts.append(attempt)
            if attempt.composite_score >= confidence_threshold:
                logger.debug("Page %d: font_program sufficient (%.1f)",
                             page_number, attempt.composite_score)
                return attempt
        except Exception as e:
            logger.debug("Page %d font_program failed: %s", page_number, e)

    # --- Strategy 2: Direct text + Unicode repair ---
    if max_attempts >= 2:
        try:
            from app.extract.direct_extract import extract_page_direct
            result = extract_page_direct(page, font_lookup)
            text = result.get("text", "")

            # Apply Unicode repair
            from app.extract.unicode_validator import repair_devanagari_unicode
            text = repair_devanagari_unicode(text)

            # Apply grammar repair
            from app.extract.devanagari_grammar import repair_grammar_issues
            text, repairs = repair_grammar_issues(text)

            scores = _score_text(text)
            attempt = ExtractionAttempt(
                strategy="direct_unicode_repair",
                text=text,
                details={"repairs": repairs},
                **scores,
            )
            attempts.append(attempt)
            if attempt.composite_score >= confidence_threshold:
                logger.debug("Page %d: direct_repair sufficient (%.1f)",
                             page_number, attempt.composite_score)
                return attempt
        except Exception as e:
            logger.debug("Page %d direct_repair failed: %s", page_number, e)

    # --- Strategy 3: Multi-engine consensus ---
    if max_attempts >= 3:
        try:
            from app.extract.multi_engine_extractor import extract_with_consensus
            consensus = extract_with_consensus(page, prefer_devanagari=True)
            text = consensus.get("text", "")

            scores = _score_text(text)
            attempt = ExtractionAttempt(
                strategy=f"consensus_{consensus.get('engine', 'unknown')}",
                text=text,
                details=consensus,
                **scores,
            )
            attempts.append(attempt)
            if attempt.composite_score >= confidence_threshold:
                logger.debug("Page %d: consensus sufficient (%.1f)",
                             page_number, attempt.composite_score)
                return attempt
        except Exception as e:
            logger.debug("Page %d consensus failed: %s", page_number, e)

    # --- Strategy 4: OCR (last resort) ---
    if max_attempts >= 4:
        try:
            from app.extract.precision_pipeline import extract_page_precision
            ocr_result = extract_page_precision(page, lang)
            text = ocr_result.get("text", "")

            scores = _score_text(text)
            attempt = ExtractionAttempt(
                strategy="ocr_fallback",
                text=text,
                details={"ocr_confidence": ocr_result.get("confidence", 0)},
                **scores,
            )
            attempts.append(attempt)
        except Exception as e:
            logger.debug("Page %d OCR failed: %s", page_number, e)

    # --- Select best attempt ---
    if not attempts:
        return ExtractionAttempt(
            strategy="none",
            text="",
            confidence=0,
            unicode_quality=0,
            word_count=0,
            devanagari_ratio=0,
        )

    best = max(attempts, key=lambda a: a.composite_score)
    logger.info(
        "Page %d: best strategy = '%s' (score=%.1f, attempts=%d)",
        page_number, best.strategy, best.composite_score, len(attempts),
    )
    return best


def _extract_via_font_programs(
    page: fitz.Page,
    font_programs: dict,
) -> str:
    """
    Extract text using font program glyph mappings.

    This is the highest-fidelity extraction method for digital PDFs —
    it reads glyph IDs from the PDF and maps them to Unicode using
    the font program's own tables, bypassing the ToUnicode CMap entirely.
    """
    from app.extract.font_program_parser import resolve_glyph_unicode

    page_dict = page.get_text(
        "rawdict",
        flags=fitz.TEXT_PRESERVE_WHITESPACE,
    )

    lines: list[str] = []

    for block in page_dict.get("blocks", []):
        if block.get("type") != 0:
            continue

        block_lines: list[str] = []
        for line in block.get("lines", []):
            line_chars: list[str] = []
            for span in line.get("spans", []):
                font_name = span.get("font", "")
                for char_dict in span.get("chars", []):
                    pdf_unicode = char_dict.get("c", "")
                    # Use font program mapping if available
                    if font_name in font_programs:
                        char_code = ord(pdf_unicode) if pdf_unicode else 0
                        glyph_name = ""  # rawdict doesn't give glyph names
                        resolved, conf, source = resolve_glyph_unicode(
                            char_code, glyph_name, font_name,
                            font_programs, pdf_unicode,
                        )
                        line_chars.append(resolved)
                    else:
                        line_chars.append(pdf_unicode)

            if line_chars:
                block_lines.append("".join(line_chars).strip())

        if block_lines:
            lines.append("\n".join(block_lines))

    return "\n\n".join(lines)
