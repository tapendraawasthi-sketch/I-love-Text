"""
Cross-Page Intelligence Engine — uses information from neighboring
pages to improve extraction quality.

Detects and handles:
- Continued tables across page breaks
- Continued sentences/paragraphs
- Repeated headers/footers (for suppression)
- Page numbering sequences (for validation)
- Consistent terminology (if "नेपाल सरकार" appears on page 1,
  "नपाल सरकार" on page 5 is likely an OCR error)

Addresses Problem 16 from the architectural critique.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from app.logging_config import get_logger

logger = get_logger("CrossPageIntelligence")

_DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]+")


@dataclass
class PageText:
    """Extracted text from a single page with metadata."""
    page_number: int
    text: str
    header: str = ""
    footer: str = ""
    first_line: str = ""
    last_line: str = ""
    word_set: set[str] = field(default_factory=set)
    has_table_at_bottom: bool = False
    has_table_at_top: bool = False
    ends_mid_sentence: bool = False


@dataclass
class CrossPageResult:
    """Result of cross-page analysis."""
    running_headers: set[str] = field(default_factory=set)
    running_footers: set[str] = field(default_factory=set)
    page_number_sequence: list[int] = field(default_factory=list)
    continued_tables: list[tuple[int, int]] = field(default_factory=list)
    continued_sentences: list[tuple[int, int]] = field(default_factory=list)
    global_vocabulary: Counter = field(default_factory=Counter)
    corrections_from_context: list[dict[str, Any]] = field(default_factory=list)


def analyze_cross_page(
    page_texts: list[PageText],
) -> CrossPageResult:
    """
    Analyze text across all pages for cross-page intelligence.
    """
    result = CrossPageResult()

    if len(page_texts) < 2:
        return result

    # 1. Detect running headers and footers
    header_counts: Counter = Counter()
    footer_counts: Counter = Counter()
    for pt in page_texts:
        if pt.header.strip():
            # Normalize: replace numbers with #
            normalized = re.sub(r"\d+", "#", pt.header.strip())
            header_counts[normalized] += 1
        if pt.footer.strip():
            normalized = re.sub(r"\d+", "#", pt.footer.strip())
            footer_counts[normalized] += 1

    threshold = max(2, len(page_texts) // 4)
    result.running_headers = {t for t, c in header_counts.items() if c >= threshold}
    result.running_footers = {t for t, c in footer_counts.items() if c >= threshold}

    # 2. Build global vocabulary
    for pt in page_texts:
        words = _DEVANAGARI_RE.findall(pt.text)
        for word in words:
            result.global_vocabulary[word] += 1

    # 3. Detect continued sentences
    for i in range(len(page_texts) - 1):
        current = page_texts[i]
        next_page = page_texts[i + 1]

        if current.ends_mid_sentence:
            result.continued_sentences.append(
                (current.page_number, next_page.page_number)
            )

    # 4. Detect continued tables
    for i in range(len(page_texts) - 1):
        current = page_texts[i]
        next_page = page_texts[i + 1]

        if current.has_table_at_bottom and next_page.has_table_at_top:
            result.continued_tables.append(
                (current.page_number, next_page.page_number)
            )

    # 5. Cross-page OCR correction
    # If a word appears frequently across the document, trust it more
    # If a similar word appears only once, it might be an OCR error
    frequent_words = {
        word for word, count in result.global_vocabulary.items()
        if count >= 3 and len(word) >= 3
    }

    for pt in page_texts:
        page_words = set(_DEVANAGARI_RE.findall(pt.text))
        for word in page_words:
            if word not in frequent_words and len(word) >= 3:
                # Check if this is a near-miss of a frequent word
                for fw in frequent_words:
                    if _edit_distance(word, fw) == 1:
                        result.corrections_from_context.append({
                            "page": pt.page_number,
                            "original": word,
                            "suggested": fw,
                            "frequency": result.global_vocabulary[fw],
                            "source": "cross_page_frequency",
                        })
                        break

    logger.info(
        "Cross-page analysis: %d running headers, %d running footers, "
        "%d continued tables, %d corrections suggested",
        len(result.running_headers),
        len(result.running_footers),
        len(result.continued_tables),
        len(result.corrections_from_context),
    )

    return result


def apply_cross_page_corrections(
    page_texts: list[str],
    cross_page: CrossPageResult,
) -> list[str]:
    """Apply cross-page corrections to all page texts."""
    corrected = list(page_texts)

    for correction in cross_page.corrections_from_context:
        page_idx = correction["page"] - 1
        if 0 <= page_idx < len(corrected):
            original = correction["original"]
            suggested = correction["suggested"]
            # Only apply if the correction is confident
            if correction["frequency"] >= 5:
                corrected[page_idx] = corrected[page_idx].replace(
                    original, suggested
                )
                logger.debug(
                    "Cross-page correction on page %d: '%s' → '%s'",
                    correction["page"], original, suggested,
                )

    return corrected


def _edit_distance(a: str, b: str) -> int:
    """Simple Levenshtein distance."""
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i]
        for j, cb in enumerate(b, 1):
            curr.append(min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = curr
    return prev[-1]
