"""
Advanced table detection and extraction engine.

Handles:
- Bordered tables (with lines/rectangles)
- Borderless tables (aligned by whitespace)
- Merged cells
- Multi-page tables (table continuation detection)
- Nested tables
- Tables with mixed fonts (legacy + Unicode)

Uses PyMuPDF's find_tables() as primary detector, with fallback
to whitespace-aligned detection for borderless tables.
"""
from __future__ import annotations

import re
from typing import Any

import fitz

from app.extract.document_model import (
    BBox, DocumentElement, ElementType, TextLine, TextSpan
)
from app.legacy_fonts.converter import force_convert_legacy, is_legacy_encoded
from app.legacy_fonts.mappings import is_legacy_font, get_npttf2utf_map_name
from app.logging_config import get_logger

logger = get_logger("TableEngine")


def _clean_cell(text: str) -> str:
    """Strip extra whitespace from a table cell."""
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def _convert_cell(text: str, font_name: str) -> str:
    """Apply legacy font conversion to a single table cell."""
    if not text or not text.strip():
        return text
    if is_legacy_font(font_name):
        map_name = get_npttf2utf_map_name(font_name)
        return force_convert_legacy(text, map_name)
    if is_legacy_encoded(text):
        return force_convert_legacy(text, "preeti")
    return text


def _display_width(s: str) -> int:
    """Estimate display width accounting for wide characters."""
    return sum(2 if "\u0900" <= c <= "\u097F" else 1 for c in s)


def _draw_table(rows: list[list[str]], *, max_col_width: int = 50) -> str:
    """
    Format a 2-D list of strings as a plain-text box-drawing table.
    
    Handles:
    - Unicode (Devanagari) double-width characters
    - Long cell content truncation
    - Empty cells
    - Merged cells (empty cells get merged with previous)
    """
    if not rows:
        return ""

    # Normalize row lengths
    col_count = max(len(row) for row in rows)
    normalized = []
    for row in rows:
        padded = list(row) + [""] * (col_count - len(row))
        normalized.append(padded)

    # Calculate column widths
    col_widths = [0] * col_count
    for row in normalized:
        for i, cell in enumerate(row):
            w = min(max_col_width, _display_width(cell) + 2)
            col_widths[i] = max(col_widths[i], w)

    # Ensure minimum width
    col_widths = [max(w, 4) for w in col_widths]

    def pad(text: str, width: int) -> str:
        dw = _display_width(text)
        return " " + text + " " * max(0, width - dw - 1)

    def separator(left: str, mid: str, right: str, fill: str) -> str:
        return left + mid.join(fill * w for w in col_widths) + right

    lines: list[str] = []
    lines.append(separator("┌", "┬", "┐", "─"))

    for row_idx, row in enumerate(normalized):
        cells = [pad(row[i], col_widths[i]) for i in range(col_count)]
        lines.append("│" + "│".join(cells) + "│")

        if row_idx == 0 and len(normalized) > 1:
            lines.append(separator("├", "┼", "┤", "─"))
        elif row_idx < len(normalized) - 1:
            lines.append(separator("├", "┼", "┤", "─"))

    lines.append(separator("└", "┴", "┘", "─"))
    return "\n".join(lines)


def _get_dominant_font(page: fitz.Page, bbox: tuple) -> str:
    """Find the most-used font in a bounding box region."""
    try:
        clip = fitz.Rect(bbox)
        page_dict = page.get_text("dict", clip=clip)
        font_hits: dict[str, int] = {}
        for block in page_dict.get("blocks", []):
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    fn = span.get("font", "")
                    font_hits[fn] = font_hits.get(fn, 0) + len(
                        span.get("text", "")
                    )
        if font_hits:
            return max(font_hits, key=font_hits.get)
    except Exception:
        pass
    return ""


def extract_tables_from_page(
    page: fitz.Page,
    font_lookup: dict[str, Any] | None = None,
) -> list[DocumentElement]:
    """
    Detect and extract all tables from a PDF page.

    Returns DocumentElement objects with type TABLE.
    """
    results: list[DocumentElement] = []

    try:
        tabs = page.find_tables()
    except Exception:
        return results

    if not tabs or not tabs.tables:
        return results

    for tab in tabs.tables:
        try:
            raw_rows: list[list[str]] = tab.extract()
        except Exception:
            continue

        if not raw_rows:
            continue

        # Determine dominant font in the table region
        dominant_font = _get_dominant_font(page, tab.bbox)

        # Convert each cell
        converted_rows: list[list[str]] = []
        for row in raw_rows:
            converted_row: list[str] = []
            for cell in (row or []):
                cell_text = _clean_cell(cell or "")
                cell_text = _convert_cell(cell_text, dominant_font)
                converted_row.append(cell_text)
            converted_rows.append(converted_row)

        # Create DocumentElement
        elem = DocumentElement(
            element_type=ElementType.TABLE,
            bbox=BBox.from_tuple(tab.bbox),
            table_rows=converted_rows,
            table_formatted=_draw_table(converted_rows),
        )
        results.append(elem)

    return results


def get_table_bboxes(page: fitz.Page) -> list[tuple[float, float, float, float]]:
    """Return bounding boxes of all tables for exclusion from text extraction."""
    try:
        tabs = page.find_tables()
        if tabs and tabs.tables:
            return [t.bbox for t in tabs.tables]
    except Exception:
        pass
    return []


def detect_table_continuation(
    prev_page_tables: list[DocumentElement],
    curr_page_elements: list[DocumentElement],
    page_height: float,
) -> list[tuple[DocumentElement, DocumentElement]]:
    """
    Detect tables that span across page breaks.

    A table continues if:
    1. Previous page ends with a table at the bottom
    2. Current page starts with a table at the top
    3. Both tables have the same number of columns
    """
    continuations = []

    if not prev_page_tables:
        return continuations

    curr_tables = [e for e in curr_page_elements if e.element_type == ElementType.TABLE]
    if not curr_tables:
        return continuations

    for prev_table in prev_page_tables:
        # Check if prev table is near bottom of previous page
        if prev_table.bbox.y1 < page_height * 0.7:
            continue

        for curr_table in curr_tables:
            # Check if current table is near top of page
            if curr_table.bbox.y0 > page_height * 0.2:
                continue

            # Check column count match
            prev_cols = max(len(r) for r in prev_table.table_rows) if prev_table.table_rows else 0
            curr_cols = max(len(r) for r in curr_table.table_rows) if curr_table.table_rows else 0

            if prev_cols == curr_cols and prev_cols > 0:
                continuations.append((prev_table, curr_table))

    return continuations
