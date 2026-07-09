"""
Benchmark Framework for TextExtract.

Provides the infrastructure for measuring extraction quality against
a corpus of reference documents. Every code change should be validated
against this benchmark before acceptance.

Metrics:
    - Character Accuracy Rate (CAR): % of characters correctly extracted
    - Word Accuracy Rate (WAR): % of words correctly extracted
    - Layout Accuracy: Reading order correctness
    - Table Accuracy: Table cell content correctness
    - Unicode Quality: % of valid Devanagari sequences
    - Legal Structure: Correct section/subsection detection

Usage:
    # Run full benchmark
    python -m app.extract.benchmark_framework --corpus ./benchmark_corpus/

    # Run single document
    python -m app.extract.benchmark_framework --file test.pdf --reference test.txt
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.logging_config import get_logger

logger = get_logger("Benchmark")


@dataclass
class DocumentBenchmark:
    """Benchmark results for a single document."""
    filename: str
    category: str               # legal_act, financial, preeti, mixed, etc.

    # Timing
    extraction_time_seconds: float = 0.0
    pages: int = 0

    # Character accuracy
    total_chars_reference: int = 0
    total_chars_extracted: int = 0
    chars_correct: int = 0
    char_accuracy: float = 0.0

    # Word accuracy
    total_words_reference: int = 0
    total_words_extracted: int = 0
    words_correct: int = 0
    word_accuracy: float = 0.0

    # Unicode quality
    unicode_valid_ratio: float = 0.0
    unicode_repairs: int = 0
    devanagari_ratio: float = 0.0

    # Extraction method used
    method: str = ""
    font_strategy: str = ""

    # Issues found
    issues: list[str] = field(default_factory=list)

    @property
    def is_pass(self) -> bool:
        """Does this document meet minimum quality thresholds?"""
        return (
            self.char_accuracy >= 0.90 and
            self.word_accuracy >= 0.85 and
            self.unicode_valid_ratio >= 0.90
        )


@dataclass
class BenchmarkSuite:
    """Results for an entire benchmark suite."""
    timestamp: str = ""
    documents: list[DocumentBenchmark] = field(default_factory=list)

    @property
    def total_documents(self) -> int:
        return len(self.documents)

    @property
    def pass_count(self) -> int:
        return sum(1 for d in self.documents if d.is_pass)

    @property
    def fail_count(self) -> int:
        return self.total_documents - self.pass_count

    @property
    def mean_char_accuracy(self) -> float:
        if not self.documents:
            return 0.0
        return sum(d.char_accuracy for d in self.documents) / len(self.documents)

    @property
    def mean_word_accuracy(self) -> float:
        if not self.documents:
            return 0.0
        return sum(d.word_accuracy for d in self.documents) / len(self.documents)

    def summary(self) -> dict[str, Any]:
        """Generate summary report."""
        return {
            "total_documents": self.total_documents,
            "passed": self.pass_count,
            "failed": self.fail_count,
            "pass_rate": f"{self.pass_count}/{self.total_documents}",
            "mean_char_accuracy": round(self.mean_char_accuracy * 100, 1),
            "mean_word_accuracy": round(self.mean_word_accuracy * 100, 1),
            "by_category": self._by_category(),
        }

    def _by_category(self) -> dict[str, dict[str, Any]]:
        """Break down results by document category."""
        categories: dict[str, list[DocumentBenchmark]] = {}
        for doc in self.documents:
            categories.setdefault(doc.category, []).append(doc)

        result = {}
        for cat, docs in categories.items():
            result[cat] = {
                "count": len(docs),
                "pass": sum(1 for d in docs if d.is_pass),
                "mean_char_accuracy": round(
                    sum(d.char_accuracy for d in docs) / len(docs) * 100, 1
                ),
                "mean_word_accuracy": round(
                    sum(d.word_accuracy for d in docs) / len(docs) * 100, 1
                ),
            }
        return result


def compute_char_accuracy(reference: str, extracted: str) -> tuple[int, int, float]:
    """
    Compute character-level accuracy between reference and extracted text.

    Uses Levenshtein-based alignment for proper comparison.
    Returns: (correct_chars, total_ref_chars, accuracy_ratio)
    """
    if not reference:
        return 0, 0, 1.0 if not extracted else 0.0

    # Simple character comparison (positional)
    # For production, use proper alignment (Needleman-Wunsch or similar)
    ref_chars = [c for c in reference if c.strip()]
    ext_chars = [c for c in extracted if c.strip()]

    if not ref_chars:
        return 0, 0, 1.0

    # Count matches using LCS (Longest Common Subsequence)
    lcs_len = _lcs_length(ref_chars, ext_chars)

    total = len(ref_chars)
    accuracy = lcs_len / total if total > 0 else 0.0

    return lcs_len, total, accuracy


def compute_word_accuracy(reference: str, extracted: str) -> tuple[int, int, float]:
    """
    Compute word-level accuracy.

    Returns: (correct_words, total_ref_words, accuracy_ratio)
    """
    ref_words = set(reference.split())
    ext_words = set(extracted.split())

    if not ref_words:
        return 0, 0, 1.0

    correct = len(ref_words & ext_words)
    total = len(ref_words)
    accuracy = correct / total if total > 0 else 0.0

    return correct, total, accuracy


def _lcs_length(a: list, b: list) -> int:
    """Longest Common Subsequence length — O(n*m) DP."""
    if not a or not b:
        return 0

    # Memory-optimized: only keep two rows
    m, n = len(a), len(b)
    prev = [0] * (n + 1)

    for i in range(1, m + 1):
        curr = [0] * (n + 1)
        for j in range(1, n + 1):
            if a[i - 1] == b[j - 1]:
                curr[j] = prev[j - 1] + 1
            else:
                curr[j] = max(prev[j], curr[j - 1])
        prev = curr

    return prev[n]


def benchmark_single_document(
    pdf_bytes: bytes,
    reference_text: str,
    filename: str = "unknown.pdf",
    category: str = "general",
) -> DocumentBenchmark:
    """
    Benchmark extraction quality against a reference text.
    """
    from app.extract.pdf_handler import extract_pdf

    bench = DocumentBenchmark(filename=filename, category=category)

    start = time.time()
    try:
        result = extract_pdf(pdf_bytes, lang="auto", mode="auto")
        extracted = result.get("text", "")
        bench.pages = result.get("pages", 0)
        bench.method = result.get("method", "unknown")
    except Exception as e:
        bench.issues.append(f"Extraction failed: {e}")
        bench.extraction_time_seconds = time.time() - start
        return bench

    bench.extraction_time_seconds = time.time() - start

    # Character accuracy
    chars_correct, total_ref, char_acc = compute_char_accuracy(reference_text, extracted)
    bench.chars_correct = chars_correct
    bench.total_chars_reference = total_ref
    bench.total_chars_extracted = len([c for c in extracted if c.strip()])
    bench.char_accuracy = char_acc

    # Word accuracy
    words_correct, total_words, word_acc = compute_word_accuracy(reference_text, extracted)
    bench.words_correct = words_correct
    bench.total_words_reference = total_words
    bench.total_words_extracted = len(extracted.split())
    bench.word_accuracy = word_acc

    # Unicode quality
    import re
    deva_re = re.compile(r"[\u0900-\u097F]")
    chars = [c for c in extracted if c.strip()]
    if chars:
        bench.devanagari_ratio = sum(1 for c in chars if deva_re.match(c)) / len(chars)

    from app.extract.unicode_validator import validate_text_block
    validation = validate_text_block(extracted)
    bench.unicode_valid_ratio = validation.get("confidence", 0) / 100.0
    bench.unicode_repairs = validation.get("repairs", 0)

    return bench
