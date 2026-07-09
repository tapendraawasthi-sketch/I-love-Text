"""
Document Object Model for PDF reconstruction.

Instead of extracting text directly, we first build a structured model
of the document, then serialize it to text. This is the core architectural
change that transforms "text extraction" into "document reconstruction."

Architecture:
    PDF → Page Object Model → Reading Order Graph → Validation → TXT

Every element on every page is classified into a semantic type, positioned
in reading order, and validated before serialization.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class ElementType(Enum):
    """Semantic type of a document element."""
    HEADING_1 = "heading_1"
    HEADING_2 = "heading_2"
    HEADING_3 = "heading_3"
    PARAGRAPH = "paragraph"
    TABLE = "table"
    LIST_ITEM = "list_item"
    FIGURE = "figure"
    CAPTION = "caption"
    FOOTNOTE = "footnote"
    HEADER = "header"
    FOOTER = "footer"
    PAGE_NUMBER = "page_number"
    MARGIN_NOTE = "margin_note"
    EQUATION = "equation"
    EMPTY = "empty"


class FontEncoding(Enum):
    """Font encoding classification."""
    UNICODE = "unicode"
    PREETI = "preeti"
    KANTIPUR = "kantipur"
    SAGARMATHA = "sagarmatha"
    HIMALI = "himali"
    AAKRITI = "aakriti"
    PCS_NEPALI = "pcsnepali"
    UNKNOWN_LEGACY = "unknown_legacy"
    UNKNOWN = "unknown"


@dataclass
class BBox:
    """Bounding box with utility methods."""
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def area(self) -> float:
        return self.width * self.height

    @property
    def center_x(self) -> float:
        return (self.x0 + self.x1) / 2.0

    @property
    def center_y(self) -> float:
        return (self.y0 + self.y1) / 2.0

    def overlaps(self, other: BBox, threshold: float = 0.1) -> bool:
        """Check if two bboxes overlap by at least threshold ratio."""
        ix0 = max(self.x0, other.x0)
        iy0 = max(self.y0, other.y0)
        ix1 = min(self.x1, other.x1)
        iy1 = min(self.y1, other.y1)
        if ix0 >= ix1 or iy0 >= iy1:
            return False
        intersection = (ix1 - ix0) * (iy1 - iy0)
        min_area = min(self.area, other.area)
        if min_area <= 0:
            return False
        return intersection / min_area >= threshold

    def contains(self, other: BBox, margin: float = 2.0) -> bool:
        """Check if self contains other (with margin tolerance)."""
        return (
            self.x0 - margin <= other.x0
            and self.y0 - margin <= other.y0
            and self.x1 + margin >= other.x1
            and self.y1 + margin >= other.y1
        )

    def vertical_overlap(self, other: BBox) -> float:
        """Fraction of vertical overlap between two bboxes."""
        overlap_top = max(self.y0, other.y0)
        overlap_bottom = min(self.y1, other.y1)
        if overlap_bottom <= overlap_top:
            return 0.0
        overlap_height = overlap_bottom - overlap_top
        min_height = min(self.height, other.height)
        if min_height <= 0:
            return 0.0
        return overlap_height / min_height

    @staticmethod
    def from_tuple(t: tuple) -> BBox:
        return BBox(t[0], t[1], t[2], t[3])


@dataclass
class TextSpan:
    """A single span of text with uniform formatting."""
    text: str
    bbox: BBox
    font_name: str = ""
    font_size: float = 0.0
    font_encoding: FontEncoding = FontEncoding.UNKNOWN
    is_bold: bool = False
    is_italic: bool = False
    color: int = 0
    confidence: float = 100.0  # 0-100, for OCR results
    converted_text: str = ""   # After legacy font conversion


@dataclass
class TextLine:
    """A line of text composed of multiple spans."""
    spans: list[TextSpan] = field(default_factory=list)
    bbox: BBox = field(default_factory=lambda: BBox(0, 0, 0, 0))

    def compute_bbox(self) -> None:
        if not self.spans:
            return
        self.bbox = BBox(
            min(s.bbox.x0 for s in self.spans),
            min(s.bbox.y0 for s in self.spans),
            max(s.bbox.x1 for s in self.spans),
            max(s.bbox.y1 for s in self.spans),
        )

    @property
    def text(self) -> str:
        """Reconstruct line text from spans with appropriate spacing."""
        if not self.spans:
            return ""
        sorted_spans = sorted(self.spans, key=lambda s: s.bbox.x0)
        parts = []
        for i, span in enumerate(sorted_spans):
            t = span.converted_text or span.text
            if i == 0:
                parts.append(t)
                continue
            prev = sorted_spans[i - 1]
            gap = span.bbox.x0 - prev.bbox.x1
            char_width = max(1.0, prev.bbox.width / max(len(prev.text), 1))
            if gap > char_width * 4:
                parts.append("\t" + t)
            elif gap > char_width * 0.3:
                parts.append(" " + t)
            else:
                parts.append(t)
        return "".join(parts)

    @property
    def dominant_font_size(self) -> float:
        if not self.spans:
            return 0.0
        # Weight by text length
        total_len = sum(len(s.text) for s in self.spans)
        if total_len == 0:
            return self.spans[0].font_size
        weighted = sum(s.font_size * len(s.text) for s in self.spans)
        return weighted / total_len

    @property
    def is_bold(self) -> bool:
        total = sum(len(s.text) for s in self.spans)
        bold = sum(len(s.text) for s in self.spans if s.is_bold)
        return bold > total * 0.5 if total > 0 else False


@dataclass
class DocumentElement:
    """A semantic element in the document (paragraph, table, heading, etc.)."""
    element_type: ElementType
    bbox: BBox
    lines: list[TextLine] = field(default_factory=list)
    # For tables
    table_rows: list[list[str]] = field(default_factory=list)
    table_formatted: str = ""
    # For figures
    figure_description: str = ""
    # For list items
    list_level: int = 0
    list_marker: str = ""
    # Metadata
    font_size: float = 0.0
    is_bold: bool = False
    column_index: int = 0  # Which column this element belongs to
    reading_order: int = 0  # Position in reading sequence
    confidence: float = 100.0
    # For footnotes
    footnote_ref: str = ""

    @property
    def text(self) -> str:
        if self.table_formatted:
            return self.table_formatted
        return "\n".join(line.text for line in self.lines if line.text.strip())

    @property
    def char_count(self) -> int:
        return len(self.text.strip())


@dataclass
class Column:
    """A detected text column on a page."""
    bbox: BBox
    index: int = 0
    elements: list[DocumentElement] = field(default_factory=list)


@dataclass
class PageModel:
    """Complete model of a single page."""
    page_number: int
    width: float
    height: float
    elements: list[DocumentElement] = field(default_factory=list)
    columns: list[Column] = field(default_factory=list)
    header_elements: list[DocumentElement] = field(default_factory=list)
    footer_elements: list[DocumentElement] = field(default_factory=list)
    footnote_elements: list[DocumentElement] = field(default_factory=list)
    # Validation
    extraction_method: str = ""  # "direct_unicode", "direct_legacy", "ocr"
    confidence: float = 0.0
    fonts_detected: list[str] = field(default_factory=list)
    legacy_fonts_detected: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def all_elements_ordered(self) -> list[DocumentElement]:
        """Return all elements in reading order."""
        body = sorted(self.elements, key=lambda e: e.reading_order)
        result = []
        # Headers first
        result.extend(sorted(self.header_elements, key=lambda e: e.bbox.x0))
        # Body in reading order
        result.extend(body)
        # Footnotes at bottom
        result.extend(sorted(self.footnote_elements, key=lambda e: e.bbox.x0))
        # Footers last
        result.extend(sorted(self.footer_elements, key=lambda e: e.bbox.x0))
        return result


@dataclass
class DocumentModel:
    """Complete model of the entire document."""
    pages: list[PageModel] = field(default_factory=list)
    # Document-level metadata
    total_pages: int = 0
    dominant_font_family: str = "unknown"
    dominant_encoding: FontEncoding = FontEncoding.UNKNOWN
    all_fonts: list[str] = field(default_factory=list)
    legacy_fonts: list[str] = field(default_factory=list)
    # Running headers/footers (appear on multiple pages)
    running_headers: set[str] = field(default_factory=set)
    running_footers: set[str] = field(default_factory=set)
    # Validation
    method: str = ""
    overall_confidence: float = 0.0
    validation_report: dict[str, Any] = field(default_factory=dict)

    def add_page(self, page: PageModel) -> None:
        self.pages.append(page)
        self.total_pages = len(self.pages)

    def detect_running_elements(self) -> None:
        """Detect headers/footers that repeat across pages."""
        if len(self.pages) < 3:
            return

        from collections import Counter
        header_texts = Counter()
        footer_texts = Counter()

        for page in self.pages:
            for h in page.header_elements:
                t = h.text.strip()
                if t and len(t) > 2:
                    header_texts[t] += 1
            for f in page.footer_elements:
                t = f.text.strip()
                if t and len(t) > 2:
                    footer_texts[t] += 1

        threshold = max(2, int(len(self.pages) * 0.25))
        self.running_headers = {t for t, c in header_texts.items() if c >= threshold}
        self.running_footers = {t for t, c in footer_texts.items() if c >= threshold}

    def serialize_to_text(
        self,
        *,
        include_page_separators: bool = True,
        include_headers_footers: bool = True,
        suppress_running: bool = True,
        include_footnotes: bool = True,
    ) -> str:
        """
        Serialize the entire document model to a human-readable text string.

        This is the FINAL step - document has already been fully reconstructed.
        """
        if suppress_running:
            self.detect_running_elements()

        parts: list[str] = []

        for page in self.pages:
            page_parts: list[str] = []

            if include_page_separators and self.total_pages > 1:
                page_parts.append(
                    f"\n{'═' * 60}\n"
                    f"  PAGE {page.page_number} / {self.total_pages}\n"
                    f"{'═' * 60}\n"
                )

            # Headers (suppress running ones)
            if include_headers_footers:
                for h in page.header_elements:
                    t = h.text.strip()
                    if t and (not suppress_running or t not in self.running_headers):
                        page_parts.append(f"[HEADER] {t}")

            # Body elements in reading order
            for elem in sorted(page.elements, key=lambda e: e.reading_order):
                t = elem.text.strip()
                if not t:
                    continue

                if elem.element_type == ElementType.HEADING_1:
                    page_parts.append(f"\n# {t}\n")
                elif elem.element_type == ElementType.HEADING_2:
                    page_parts.append(f"\n## {t}\n")
                elif elem.element_type == ElementType.HEADING_3:
                    page_parts.append(f"\n### {t}\n")
                elif elem.element_type == ElementType.TABLE:
                    page_parts.append(f"\n{t}\n")
                elif elem.element_type == ElementType.LIST_ITEM:
                    indent = "  " * max(0, elem.list_level - 1)
                    marker = elem.list_marker or "•"
                    page_parts.append(f"{indent}{marker} {t}")
                elif elem.element_type == ElementType.CAPTION:
                    page_parts.append(f"[Caption] {t}")
                elif elem.element_type == ElementType.FIGURE:
                    page_parts.append(f"[Figure: {elem.figure_description or 'image'}]")
                elif elem.element_type == ElementType.EQUATION:
                    page_parts.append(f"[Equation] {t}")
                else:
                    page_parts.append(t)

            # Footnotes
            if include_footnotes and page.footnote_elements:
                page_parts.append("\n" + "─" * 40)
                for fn in sorted(page.footnote_elements, key=lambda e: e.bbox.x0):
                    t = fn.text.strip()
                    if t:
                        ref = fn.footnote_ref
                        page_parts.append(f"[{ref}] {t}" if ref else t)

            # Footers (suppress running ones)
            if include_headers_footers:
                for f in page.footer_elements:
                    t = f.text.strip()
                    if t and (not suppress_running or t not in self.running_footers):
                        page_parts.append(f"\n[FOOTER] {t}")

            if page_parts:
                parts.append("\n".join(page_parts))

        text = "\n\n".join(parts)
        # Normalize excessive blank lines
        text = re.sub(r"\n{4,}", "\n\n\n", text)
        return text.strip() + "\n"
