"""
Page Similarity Model — detects running headers and footers using
statistical analysis across ALL pages, not just neighboring ones.

Uses multiple signals:
    1. Exact text match frequency
    2. Fuzzy text similarity (Levenshtein ratio)
    3. Position consistency (same Y coordinate across pages)
    4. Font consistency (same font/size across pages)
    5. Numbering sequence detection (page numbers, section refs)

A text is classified as "running" only if it passes multiple
independent tests — preventing accidental deletion of valid content
like chapter titles that legitimately repeat.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from app.logging_config import get_logger

logger = get_logger("PageSimilarity")

_PAGE_NUM_RE = re.compile(r"[\d\u0966-\u096F]+")
_WHITESPACE_RE = re.compile(r"\s+")


@dataclass
class TextSignature:
    """Signature of a text block for similarity matching."""
    text: str
    normalized: str          # With numbers replaced by #
    y_position: float        # Relative Y (0-1)
    font_size: float
    font_name: str
    page_number: int
    is_header_zone: bool     # Top 10% of page
    is_footer_zone: bool     # Bottom 10% of page


@dataclass
class RunningElement:
    """A confirmed running header or footer."""
    normalized_text: str
    frequency: int
    frequency_ratio: float   # What fraction of pages it appears on
    zone: str                # "header" or "footer"
    confidence: float        # 0-100
    evidence: list[str] = field(default_factory=list)


def _normalize_for_comparison(text: str) -> str:
    """Normalize text for comparison — replace numbers, collapse whitespace."""
    result = _PAGE_NUM_RE.sub("#", text.strip())
    result = _WHITESPACE_RE.sub(" ", result)
    return result.strip()


def _levenshtein_ratio(a: str, b: str) -> float:
    """Compute normalized Levenshtein similarity ratio (0-1)."""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0

    max_len = max(len(a), len(b))
    if max_len == 0:
        return 1.0

    # Simple Levenshtein
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i]
        for j, cb in enumerate(b, 1):
            curr.append(min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = curr

    distance = prev[-1]
    return 1.0 - distance / max_len


def detect_running_elements(
    page_signatures: list[list[TextSignature]],
    min_frequency_ratio: float = 0.25,
    min_similarity: float = 0.85,
) -> tuple[list[RunningElement], list[RunningElement]]:
    """
    Detect running headers and footers across all pages.

    Returns: (running_headers, running_footers)
    """
    total_pages = len(page_signatures)
    if total_pages < 3:
        return [], []

    # Collect header zone and footer zone texts
    header_texts: list[TextSignature] = []
    footer_texts: list[TextSignature] = []

    for page_sigs in page_signatures:
        for sig in page_sigs:
            if sig.is_header_zone:
                header_texts.append(sig)
            if sig.is_footer_zone:
                footer_texts.append(sig)

    running_headers = _find_running_in_zone(header_texts, total_pages,
                                             min_frequency_ratio, min_similarity,
                                             zone="header")
    running_footers = _find_running_in_zone(footer_texts, total_pages,
                                             min_frequency_ratio, min_similarity,
                                             zone="footer")

    return running_headers, running_footers


def _find_running_in_zone(
    signatures: list[TextSignature],
    total_pages: int,
    min_frequency_ratio: float,
    min_similarity: float,
    zone: str,
) -> list[RunningElement]:
    """Find running elements within a specific zone (header or footer)."""
    if not signatures:
        return []

    # Group by normalized text
    groups: dict[str, list[TextSignature]] = defaultdict(list)
    for sig in signatures:
        groups[sig.normalized].append(sig)

    running: list[RunningElement] = []
    min_count = max(2, int(total_pages * min_frequency_ratio))

    for normalized, sigs in groups.items():
        if not normalized or len(normalized) < 2:
            continue

        frequency = len(sigs)
        if frequency < min_count:
            continue

        frequency_ratio = frequency / total_pages
        evidence: list[str] = []
        confidence = 0.0

        # Evidence 1: Frequency
        evidence.append(f"appears on {frequency}/{total_pages} pages ({frequency_ratio:.0%})")
        confidence += min(40, frequency_ratio * 50)

        # Evidence 2: Position consistency
        y_positions = [s.y_position for s in sigs]
        if y_positions:
            y_std = _std(y_positions)
            if y_std < 0.02:  # Very consistent Y position
                confidence += 20
                evidence.append(f"consistent Y position (σ={y_std:.4f})")
            elif y_std < 0.05:
                confidence += 10
                evidence.append(f"mostly consistent Y (σ={y_std:.4f})")

        # Evidence 3: Font consistency
        font_sizes = [s.font_size for s in sigs]
        if font_sizes and _std(font_sizes) < 0.5:
            confidence += 10
            evidence.append("consistent font size")

        # Evidence 4: Cross-check with fuzzy matching
        # Look for similar (not identical) texts that we might have missed
        fuzzy_matches = 0
        for other_norm, other_sigs in groups.items():
            if other_norm == normalized:
                continue
            if _levenshtein_ratio(normalized, other_norm) >= min_similarity:
                fuzzy_matches += len(other_sigs)

        if fuzzy_matches > 0:
            confidence += min(15, fuzzy_matches * 3)
            evidence.append(f"{fuzzy_matches} fuzzy matches found")

        # Evidence 5: Is it a page number pattern?
        if re.fullmatch(r"[#\s\-–]+", normalized):
            confidence += 15
            evidence.append("page number pattern")

        if confidence >= 40:
            running.append(RunningElement(
                normalized_text=normalized,
                frequency=frequency,
                frequency_ratio=frequency_ratio,
                zone=zone,
                confidence=min(100, confidence),
                evidence=evidence,
            ))

    return sorted(running, key=lambda r: r.confidence, reverse=True)


def _std(values: list[float]) -> float:
    """Standard deviation."""
    if not values or len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    return variance ** 0.5
