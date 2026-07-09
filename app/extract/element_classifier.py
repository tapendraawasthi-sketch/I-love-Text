"""
Element type classifier: identifies headings, lists, captions,
figures, footnotes, and equations from raw text blocks.

Uses font size, position, content patterns, and context to classify
each DocumentElement into its semantic type.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any

from app.extract.document_model import (
    DocumentElement, ElementType, TextLine, BBox
)
from app.logging_config import get_logger

logger = get_logger("ElementClassifier")

# --- List patterns ---
# Nepali numbering: क., ख., ग., or (क), (ख)
_NEPALI_LIST_RE = re.compile(
    r'^[\s]*'
    r'(?:'
    r'[\u0915-\u0939][\.\)]\s'           # क. ख. ग.
    r'|\([\u0915-\u0939]\)\s'            # (क) (ख) (ग)
    r'|[\u0966-\u096F]+[\.\)]\s'         # १. २. ३.
    r'|\d+[\.\)]\s'                       # 1. 2. 3.
    r'|[a-zA-Z][\.\)]\s'                 # a. b. c.
    r'|\([a-zA-Z]\)\s'                   # (a) (b) (c)
    r'|\(\d+\)\s'                        # (1) (2) (3)
    r'|[ivxIVX]+[\.\)]\s'               # i. ii. iii.
    r'|\([ivxIVX]+\)\s'                 # (i) (ii) (iii)
    r'|[-•●○▪▸►]\s'                     # Bullet markers
    r')'
)

_NESTED_LIST_RE = re.compile(
    r'^[\s]*'
    r'(?:'
    r'\d+\.\d+[\.\)]\s'                  # 1.1. 1.2.
    r'|\d+\.\d+\.\d+[\.\)]\s'           # 1.1.1. 1.1.2.
    r'|\([a-z]\)\s'                      # (a) at nested level
    r'|\([ivx]+\)\s'                     # (i) (ii) at nested level
    r')'
)

# --- Caption patterns ---
_CAPTION_RE = re.compile(
    r'^(?:'
    r'(?:Figure|Fig\.?|Table|Chart|Graph|Diagram|Map|Photo|Image)\s*\d'
    r'|(?:||||||)\s*[\u0966-\u096F\d]'
    r'|(?:Source|:|Note|:|Reference|)\s*:'
    r')',
    re.IGNORECASE
)

# --- Equation indicators ---
_EQUATION_RE = re.compile(
    r'(?:'
    r'[=<>≤≥≠±∓×÷∑∏∫∂√∞]'
    r'|\\(?:frac|sqrt|sum|int|alpha|beta|gamma|delta)'
    r')'
)


def classify_element(
    elem: DocumentElement,
    body_font_size: float,
    page_width: float,
    prev_elem: DocumentElement | None = None,
    next_elem: DocumentElement | None = None,
) -> ElementType:
    """
    Classify a single element into its semantic type.
    
    Uses font size, content patterns, and surrounding context.
    """
    text = elem.text.strip()
    if not text:
        return ElementType.EMPTY

    # --- Heading detection ---
    if elem.font_size > 0 and body_font_size > 0:
        size_ratio = elem.font_size / body_font_size

        if size_ratio >= 1.6:
            return ElementType.HEADING_1
        elif size_ratio >= 1.3:
            return ElementType.HEADING_2
        elif size_ratio >= 1.1 and elem.is_bold:
            return ElementType.HEADING_3
        elif elem.is_bold and len(text) < 100 and body_font_size > 0:
            # Bold short text at body size = possible H3
            if _looks_like_heading(text):
                return ElementType.HEADING_3

    # --- List detection ---
    if _NESTED_LIST_RE.match(text):
        elem.list_level = _detect_list_level(text)
        elem.list_marker = _extract_list_marker(text)
        return ElementType.LIST_ITEM

    if _NEPALI_LIST_RE.match(text):
        elem.list_level = 1
        elem.list_marker = _extract_list_marker(text)
        return ElementType.LIST_ITEM

    # --- Caption detection ---
    if _CAPTION_RE.match(text):
        return ElementType.CAPTION

    # Also detect captions by position: small text immediately after a figure/table
    if prev_elem and prev_elem.element_type in (ElementType.FIGURE, ElementType.TABLE):
        if elem.font_size > 0 and elem.font_size < body_font_size * 0.9:
            if len(text) < 200:
                return ElementType.CAPTION

    # --- Equation detection ---
    if _EQUATION_RE.search(text) and len(text) < 200:
        # Count equation symbols vs text
        eq_chars = len(_EQUATION_RE.findall(text))
        if eq_chars / max(len(text), 1) > 0.15:
            return ElementType.EQUATION

    # --- Default: paragraph ---
    return ElementType.PARAGRAPH


def _looks_like_heading(text: str) -> bool:
    """Heuristic: short bold text that looks like a heading."""
    # No period at end (headings don't usually end with full stop)
    if text.endswith('.') or text.endswith('।'):
        return False
    # Not too long
    if len(text) > 120:
        return False
    # Has some alphanumeric content
    if not re.search(r'[\w\u0900-\u097F]', text):
        return False
    return True


def _detect_list_level(text: str) -> int:
    """Detect nesting level of a list item."""
    # Count leading whitespace
    stripped = text.lstrip()
    indent = len(text) - len(stripped)

    # Numbered patterns indicate level
    if re.match(r'\d+\.\d+\.\d+', stripped):
        return 3
    if re.match(r'\d+\.\d+', stripped):
        return 2
    if re.match(r'\([a-z]\)', stripped):
        return 2
    if re.match(r'\([ivx]+\)', stripped):
        return 3

    # Use indent as fallback
    if indent > 8:
        return 3
    elif indent > 4:
        return 2
    return 1


def _extract_list_marker(text: str) -> str:
    """Extract the list marker from a list item."""
    match = _NEPALI_LIST_RE.match(text) or _NESTED_LIST_RE.match(text)
    if match:
        return match.group().strip()
    return ""


def compute_body_font_size(elements: list[DocumentElement]) -> float:
    """
    Determine the body text font size (mode of all font sizes).
    """
    sizes = []
    for elem in elements:
        if elem.font_size > 0:
            # Weight by text length
            text_len = len(elem.text)
            sizes.extend([round(elem.font_size * 2) / 2] * text_len)

    if not sizes:
        return 10.0

    counter = Counter(sizes)
    return counter.most_common(1)[0][0]


def classify_all_elements(
    elements: list[DocumentElement],
    page_width: float,
) -> None:
    """
    Classify all elements on a page.
    Modifies elements in place.
    """
    body_size = compute_body_font_size(elements)

    for i, elem in enumerate(elements):
        if elem.element_type == ElementType.TABLE:
            continue  # Tables already classified

        prev_elem = elements[i - 1] if i > 0 else None
        next_elem = elements[i + 1] if i < len(elements) - 1 else None

        elem.element_type = classify_element(
            elem, body_size, page_width, prev_elem, next_elem
        )
