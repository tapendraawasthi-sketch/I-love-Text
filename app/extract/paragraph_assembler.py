"""
Paragraph assembler — reconstructs paragraphs from word objects.

Key insight: PDFs store lines, not paragraphs. A justified text paragraph
may be split across 5+ lines. We need to rejoin them.

Strategy:
    1. Group words into visual lines (same Y coordinate)
    2. Detect paragraph boundaries:
       - Significant vertical gap between lines
       - Indent change (new paragraph starts with indent)
       - Ends with purna viram (।) — Nepali sentence terminator
       - Heading detection (different font size)
    3. Join lines within same paragraph with spaces (not newlines)
    4. Preserve intentional line breaks (poetry, addresses, lists)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.extract.glyph_model import WordObject, ParagraphObject, SentenceObject
from app.logging_config import get_logger

logger = get_logger("ParagraphAssembler")

# Nepali sentence terminators
_SENTENCE_END_RE = re.compile(r"[।॥.?!]$")
# Line-continuation indicators (word fragments, mid-sentence)
_CONTINUATION_RE = re.compile(r"[\u0915-\u0939\u093E-\u094F]$")  # Ends with consonant/matra


@dataclass
class VisualLine:
    """A visual line of text — words that share the same baseline."""
    words: list[WordObject] = field(default_factory=list)
    y_baseline: float = 0.0
    x_start: float = 0.0
    x_end: float = 0.0
    font_size: float = 0.0
    is_bold: bool = False

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)

    @property
    def indent(self) -> float:
        """Left margin indent."""
        return self.x_start

    def compute_metrics(self) -> None:
        if not self.words:
            return
        self.x_start = min(w.x0 for w in self.words)
        self.x_end = max(w.x1 for w in self.words)
        # Font size from first word's first glyph
        for w in self.words:
            if w.glyphs:
                self.font_size = w.glyphs[0].font_size
                self.is_bold = w.glyphs[0].is_bold
                break


def group_words_into_lines(
    words: list[WordObject],
    line_tolerance: float = 0.5,
) -> list[VisualLine]:
    """
    Group words into visual lines based on Y-coordinate proximity.

    words should already be sorted by (y, x).
    """
    if not words:
        return []

    lines: list[VisualLine] = []
    current_line = VisualLine()

    for word in sorted(words, key=lambda w: (w.y0, w.x0)):
        if not current_line.words:
            current_line.words.append(word)
            current_line.y_baseline = word.y1  # Use bottom as baseline
            continue

        # Check if same line (Y overlap)
        prev_y = current_line.y_baseline
        height = max(1.0, word.y1 - word.y0)
        if abs(word.y1 - prev_y) < height * line_tolerance:
            current_line.words.append(word)
        else:
            current_line.compute_metrics()
            lines.append(current_line)
            current_line = VisualLine(words=[word], y_baseline=word.y1)

    if current_line.words:
        current_line.compute_metrics()
        lines.append(current_line)

    return lines


def detect_paragraph_boundaries(
    lines: list[VisualLine],
    page_width: float,
    body_font_size: float = 0.0,
) -> list[list[VisualLine]]:
    """
    Group visual lines into paragraphs.

    Paragraph boundary signals:
    1. Large vertical gap (> 1.5x line height)
    2. Indent change (next line has different left margin)
    3. Previous line ends with sentence terminator AND is short
    4. Font size change (heading → body or body → heading)
    5. Empty line between blocks
    """
    if not lines:
        return []

    if not body_font_size:
        sizes = [l.font_size for l in lines if l.font_size > 0]
        body_font_size = max(set(sizes), key=sizes.count) if sizes else 10.0

    paragraphs: list[list[VisualLine]] = []
    current_para: list[VisualLine] = []

    for i, line in enumerate(lines):
        if not current_para:
            current_para.append(line)
            continue

        prev_line = current_para[-1]

        # Signal 1: Large vertical gap
        gap = line.y_baseline - prev_line.y_baseline
        avg_height = body_font_size * 1.4  # Approximate line height
        is_large_gap = gap > avg_height * 1.8

        # Signal 2: Indent change
        margin_left = min(l.x_start for l in lines) if lines else 0
        prev_indent = prev_line.x_start - margin_left
        curr_indent = line.x_start - margin_left
        has_new_indent = curr_indent > prev_indent + body_font_size * 1.5

        # Signal 3: Previous line ends with terminator and is short
        prev_text = prev_line.text.strip()
        ends_sentence = bool(_SENTENCE_END_RE.search(prev_text))
        page_right = max(l.x_end for l in lines) if lines else page_width
        line_width_ratio = (prev_line.x_end - margin_left) / max(1, page_right - margin_left)
        is_short_line = line_width_ratio < 0.85  # Not full-width justified

        # Signal 4: Font size change
        font_change = abs(line.font_size - prev_line.font_size) > 1.0

        # Decision
        is_new_paragraph = (
            is_large_gap or
            has_new_indent or
            (ends_sentence and is_short_line) or
            font_change
        )

        if is_new_paragraph:
            paragraphs.append(current_para)
            current_para = [line]
        else:
            current_para.append(line)

    if current_para:
        paragraphs.append(current_para)

    return paragraphs


def assemble_paragraphs(
    words: list[WordObject],
    page_width: float,
) -> list[ParagraphObject]:
    """
    Full pipeline: words → lines → paragraphs.

    Lines within a paragraph are joined with spaces (not newlines).
    This is the key difference from simple line extraction.
    """
    lines = group_words_into_lines(words)
    para_groups = detect_paragraph_boundaries(lines, page_width)

    paragraphs: list[ParagraphObject] = []

    for group in para_groups:
        if not group:
            continue

        # Join lines within paragraph with spaces
        all_words: list[WordObject] = []
        for line in group:
            all_words.extend(line.words)

        # Build paragraph text (lines joined with space, not newline)
        para_text = " ".join(line.text for line in group)

        # Create sentence objects (split on purna viram)
        sentence_texts = re.split(r"(?<=[।॥.?!])\s*", para_text)
        sentences = []
        for st in sentence_texts:
            if st.strip():
                sentence = SentenceObject(
                    words=[WordObject(glyphs=[], x0=0, y0=0, x1=0, y1=0)],
                    is_complete=bool(_SENTENCE_END_RE.search(st)),
                )
                # Store the actual text by setting a simple word
                sentence.words[0] = WordObject()
                # We'll use the text property through a simplified approach
                sentences.append(sentence)

        para = ParagraphObject(
            sentences=sentences,
            x0=min(l.x_start for l in group),
            y0=min(l.y_baseline for l in group),
            x1=max(l.x_end for l in group),
            y1=max(l.y_baseline for l in group),
            font_size=group[0].font_size,
            is_bold=group[0].is_bold,
        )

        # Store the joined text directly
        para._joined_text = para_text
        paragraphs.append(para)

    return paragraphs
