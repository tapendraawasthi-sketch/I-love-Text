"""
Probabilistic header/footer detection.

Instead of simply deleting repeated lines, each text block receives a
probability of being a header, footer, body, footnote, or page number.

This prevents accidental deletion of meaningful repeated text like
chapter titles that appear on every page.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

from app.extract.document_model import (
    DocumentElement, ElementType, PageModel, DocumentModel, BBox
)
from app.logging_config import get_logger

logger = get_logger("HeaderFooterDetector")

# Common page number patterns (both English and Nepali digits)
_PAGE_NUM_RE = re.compile(
    r'^[\s\-–—]*'
    r'(?:'
    r'(?:page|p\.?|pag)\s*\d+'         # English page numbers
    r'|\d{1,4}'                          # Bare numbers
    r'|[- ]+\d{1,4}[- ]+'              # Dashes around numbers
    r'|[\u0966-\u096F]{1,4}'            # Nepali digits only
    r'|[-–— ]+[\u0966-\u096F]{1,4}[-–— ]+'  # Nepali with dashes
    r')'
    r'[\s\-–—]*$',
    re.IGNORECASE
)

# Common header/footer phrases
_HEADER_PHRASES = re.compile(
    r'(chapter\s+\d|section\s+\d|part\s+[ivx\d]'
    r'||||||)',
    re.IGNORECASE
)


@dataclass
class ElementProbability:
    """Probability assignment for a document element."""
    element: DocumentElement
    p_header: float = 0.0
    p_footer: float = 0.0
    p_body: float = 0.0
    p_page_number: float = 0.0
    p_footnote: float = 0.0

    @property
    def most_likely(self) -> ElementType:
        probs = {
            ElementType.HEADER: self.p_header,
            ElementType.FOOTER: self.p_footer,
            ElementType.PARAGRAPH: self.p_body,
            ElementType.PAGE_NUMBER: self.p_page_number,
            ElementType.FOOTNOTE: self.p_footnote,
        }
        return max(probs, key=probs.get)


def classify_element_probability(
    elem: DocumentElement,
    page_height: float,
    page_width: float,
    body_font_size: float = 10.0,
) -> ElementProbability:
    """
    Assign probabilities to a single element based on position and content.
    """
    prob = ElementProbability(element=elem)
    text = elem.text.strip()
    mid_y = elem.bbox.center_y
    rel_y = mid_y / page_height if page_height > 0 else 0.5

    # Position-based priors
    if rel_y < 0.08:
        prob.p_header = 0.7
        prob.p_body = 0.2
        prob.p_footer = 0.0
    elif rel_y > 0.92:
        prob.p_footer = 0.6
        prob.p_body = 0.15
        prob.p_page_number = 0.2
    elif rel_y > 0.80:
        prob.p_body = 0.4
        prob.p_footnote = 0.3
        prob.p_footer = 0.2
    else:
        prob.p_body = 0.85
        prob.p_header = 0.05
        prob.p_footer = 0.05

    # Content-based adjustments
    if text:
        # Page number detection
        if _PAGE_NUM_RE.match(text):
            prob.p_page_number = max(prob.p_page_number, 0.85)
            prob.p_body = min(prob.p_body, 0.1)

        # Short text in margins is likely header/footer
        if len(text) < 50 and rel_y < 0.1:
            prob.p_header += 0.15

        if len(text) < 50 and rel_y > 0.9:
            prob.p_footer += 0.15

        # Header phrases
        if _HEADER_PHRASES.search(text):
            prob.p_header += 0.2

        # Footnote indicators: superscript numbers at start
        if re.match(r'^[\d\*†‡]+[\.\)]\s', text):
            prob.p_footnote += 0.3
            if rel_y > 0.7:
                prob.p_footnote += 0.2

        # Small font = more likely footnote
        if elem.font_size > 0 and body_font_size > 0:
            if elem.font_size < body_font_size * 0.8:
                prob.p_footnote += 0.2
            if elem.font_size > body_font_size * 1.3:
                prob.p_header += 0.1  # Larger text in header zone

        # Long paragraphs are almost certainly body
        if len(text) > 200:
            prob.p_body = max(prob.p_body, 0.9)
            prob.p_header = min(prob.p_header, 0.05)
            prob.p_footer = min(prob.p_footer, 0.05)

    # Normalize
    total = (prob.p_header + prob.p_footer + prob.p_body +
             prob.p_page_number + prob.p_footnote)
    if total > 0:
        prob.p_header /= total
        prob.p_footer /= total
        prob.p_body /= total
        prob.p_page_number /= total
        prob.p_footnote /= total

    return prob


def classify_page_elements(
    page: PageModel,
    body_font_size: float = 10.0,
) -> None:
    """
    Classify all elements on a page using probabilistic detection.
    Modifies elements in place.
    """
    for elem in page.elements:
        prob = classify_element_probability(
            elem, page.height, page.width, body_font_size
        )
        classification = prob.most_likely

        if classification == ElementType.HEADER:
            page.elements.remove(elem)
            page.header_elements.append(elem)
            elem.element_type = ElementType.HEADER
        elif classification == ElementType.FOOTER:
            page.elements.remove(elem)
            page.footer_elements.append(elem)
            elem.element_type = ElementType.FOOTER
        elif classification == ElementType.PAGE_NUMBER:
            page.elements.remove(elem)
            page.footer_elements.append(elem)
            elem.element_type = ElementType.PAGE_NUMBER
        elif classification == ElementType.FOOTNOTE:
            page.elements.remove(elem)
            page.footnote_elements.append(elem)
            elem.element_type = ElementType.FOOTNOTE


def detect_running_elements_smart(
    doc: DocumentModel,
) -> None:
    """
    Detect running headers/footers across pages.

    Unlike simple repetition detection, this uses fuzzy matching
    to handle cases where page numbers change but surrounding text stays.
    """
    if len(doc.pages) < 3:
        return

    header_texts = Counter()
    footer_texts = Counter()

    for page in doc.pages:
        for h in page.header_elements:
            t = _normalize_for_comparison(h.text)
            if t:
                header_texts[t] += 1
        for f in page.footer_elements:
            t = _normalize_for_comparison(f.text)
            if t:
                footer_texts[t] += 1

    threshold = max(2, int(len(doc.pages) * 0.25))

    # Also detect patterns like "Chapter X - Title" where X changes
    doc.running_headers = set()
    doc.running_footers = set()

    for text, count in header_texts.items():
        if count >= threshold:
            doc.running_headers.add(text)

    for text, count in footer_texts.items():
        if count >= threshold:
            doc.running_footers.add(text)


def _normalize_for_comparison(text: str) -> str:
    """Normalize text for running element comparison."""
    text = text.strip()
    # Remove page numbers (both English and Nepali)
    text = re.sub(r'\d+', '#', text)
    text = re.sub(r'[\u0966-\u096F]+', '#', text)
    # Normalize whitespace
    text = re.sub(r'\s+', ' ', text)
    return text.strip()
