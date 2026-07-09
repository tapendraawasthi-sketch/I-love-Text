"""
Table detection and extraction for PDF pages using PyMuPDF.

Detects tables on a page, extracts their cell content (with legacy font
conversion applied), and formats them as plain-text aligned tables suitable
for a .txt file that a human can read.

The output for a detected table looks like:

  ┌──────────────┬──────────────┬──────────────┐
  │ Col 1        │ Col 2        │ Col 3        │
  ├──────────────┼──────────────┼──────────────┤
  │ नेपाल सरकार  │ 2081         │ 1,20,000     │
  └──────────────┴──────────────┴──────────────┘
"""
from __future__ import annotations

import re
from typing import Any

import fitz

from app.legacy_fonts.converter import force_convert_legacy, is_legacy_encoded
from app.legacy_fonts.mappings import is_legacy_font, get_npttf2utf_map_name


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


def _draw_table(rows: list[list[str]]) -> str:
    """
    Format a 2-D list of strings as a plain-text box-drawing table.
    Handles Unicode (Devanagari) characters which are double-width in most
    terminal fonts by estimating display width.
    """
    if not rows:
        return ""

    def display_width(s: str) -> int:
        # Devanagari codepoints are double-width in most monospace fonts
        return sum(2 if "\u0900" <= c <= "\u097F" else 1 for c in s)

    # Calculate column widths
    col_count = max(len(row) for row in rows)
    col_widths = [0] * col_count
    for row in rows:
        for i, cell in enumerate(row):
            col_widths[i] = max(col_widths[i], display_width(cell) + 2)

    def pad(text: str, width: int) -> str:
        dw = display_width(text)
        return " " + text + " " * (width - dw - 1)

    def separator(left: str, mid: str, right: str, fill: str) -> str:
        return left + mid.join(fill * w for w in col_widths) + right

    lines: list[str] = []
    lines.append(separator("┌", "┬", "┐", "─"))
    for row_idx, row in enumerate(rows):
        cells = [pad(row[i] if i < len(row) else "", col_widths[i])
                 for i in range(col_count)]
        lines.append("│" + "│".join(cells) + "│")
        if row_idx == 0 and len(rows) > 1:
            lines.append(separator("├", "┼", "┤", "─"))
    lines.append(separator("└", "┴", "┘", "─"))
    return "\n".join(lines)


def extract_tables_from_page(
    page: fitz.Page,
    font_lookup: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """
    Detect and extract all tables from a PDF page.

    Returns a list of dicts:
        {
            "bbox": (x0, y0, x1, y1),      # table bounding box
            "rows": [[str, ...], ...],       # 2-D list of cell text
            "formatted": str,                # ready-to-print box table
        }
    """
    results: list[dict[str, Any]] = []

    try:
        tabs = page.find_tables()
    except Exception:
        return results  # fitz version may not support find_tables

    if not tabs or not tabs.tables:
        return results

    for tab in tabs.tables:
        try:
            raw_rows: list[list[str]] = tab.extract()
        except Exception:
            continue

        if not raw_rows:
            continue

        # Determine dominant font in the table bbox by scanning the text dict
        dominant_font = ""
        try:
            clip = fitz.Rect(tab.bbox)
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
                dominant_font = max(font_hits, key=font_hits.get)
        except Exception:
            pass

        # Convert each cell
        converted_rows: list[list[str]] = []
        for row in raw_rows:
            converted_row: list[str] = []
            for cell in (row or []):
                cell_text = _clean_cell(cell or "")
                cell_text = _convert_cell(cell_text, dominant_font)
                converted_row.append(cell_text)
            converted_rows.append(converted_row)

        results.append({
            "bbox": tab.bbox,
            "rows": converted_rows,
            "formatted": _draw_table(converted_rows),
        })

    return results


def get_table_bboxes(page: fitz.Page) -> list[tuple[float, float, float, float]]:
    """
    Return the bounding boxes of all tables on the page.
    Used by the text extractor to SKIP table regions (so they are not
    double-extracted as raw text).
    """
    try:
        tabs = page.find_tables()
        if tabs and tabs.tables:
            return [t.bbox for t in tabs.tables]
    except Exception:
        pass
    return []
