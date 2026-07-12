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

Two detection strategies are used:

1. Ruling-line tables: PyMuPDF's `page.find_tables()`, which relies on
   drawn borders/ruling lines. This is accurate when it fires, but many
   real-world Nepali government/financial documents use whitespace-aligned
   columns with no drawn borders at all, which `find_tables()` silently
   fails to detect (it just returns no tables, with no way to tell the
   difference between "no table on this page" and "a table PyMuPDF
   couldn't see").

2. Borderless/whitespace-aligned tables (fallback): a column-clustering
   heuristic that only runs when (1) finds nothing on a page. It groups
   words into lines, looks for several consecutive lines whose word
   x-positions line up into consistent column boundaries, and treats that
   as a table. This intentionally requires several consistent rows before
   triggering, to avoid mistaking ordinary two-column body text or
   justified paragraphs for a table.
"""
from __future__ import annotations

import re
import statistics
from typing import Any

import fitz

from app.legacy_fonts.converter import force_convert_legacy, is_legacy_encoded
from app.legacy_fonts.mappings import is_legacy_font, get_npttf2utf_map_name

# --- Borderless-table fallback tuning ---------------------------------
# Minimum number of consecutive lines with matching column structure
# before we trust it's actually a table rather than coincidentally
# aligned prose.
_MIN_TABLE_ROWS = 3
# A gap between two words on the same line counts as a column boundary
# once it is at least this many points wide.
_MIN_COLUMN_GAP_PT = 24.0
# Two lines are considered to have "the same" column layout if their
# column boundary x-positions are within this tolerance of each other.
_COLUMN_ALIGN_TOLERANCE_PT = 12.0


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


def _dominant_font_in_bbox(page: fitz.Page, bbox: tuple[float, float, float, float]) -> str:
    """Scan the text dict inside bbox and return the most frequent font name."""
    dominant_font = ""
    try:
        clip = fitz.Rect(bbox)
        page_dict = page.get_text("dict", clip=clip)
        font_hits: dict[str, int] = {}
        for block in page_dict.get("blocks", []):
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    fn = span.get("font", "")
                    font_hits[fn] = font_hits.get(fn, 0) + len(span.get("text", ""))
        if font_hits:
            dominant_font = max(font_hits, key=font_hits.get)
    except Exception:
        pass
    return dominant_font


def _convert_ruling_line_table(page: fitz.Page, tab: Any) -> dict[str, Any] | None:
    """Extract + convert a single PyMuPDF ruling-line table."""
    try:
        raw_rows: list[list[str]] = tab.extract()
    except Exception:
        return None

    if not raw_rows:
        return None

    dominant_font = _dominant_font_in_bbox(page, tab.bbox)

    converted_rows: list[list[str]] = []
    for row in raw_rows:
        converted_row: list[str] = []
        for cell in (row or []):
            cell_text = _clean_cell(cell or "")
            cell_text = _convert_cell(cell_text, dominant_font)
            converted_row.append(cell_text)
        converted_rows.append(converted_row)

    return {
        "bbox": tab.bbox,
        "rows": converted_rows,
        "formatted": _draw_table(converted_rows),
        "detected_by": "ruling_lines",
    }


def _group_words_into_lines(
    words: list[tuple[float, float, float, float, str, int, int, int]],
    y_tolerance: float = 3.0,
) -> list[list[tuple[float, float, float, float, str]]]:
    """
    Group PyMuPDF `page.get_text("words")` output into visual lines by y0,
    each line sorted left-to-right by x0.
    """
    if not words:
        return []

    sorted_words = sorted(words, key=lambda w: (round(w[1] / y_tolerance), w[0]))
    lines: list[list[tuple[float, float, float, float, str]]] = []
    current: list[tuple[float, float, float, float, str]] = []
    current_y: float | None = None

    for w in sorted_words:
        x0, y0, x1, y1, text = w[0], w[1], w[2], w[3], w[4]
        if current_y is None or abs(y0 - current_y) <= y_tolerance:
            current.append((x0, y0, x1, y1, text))
            current_y = y0 if current_y is None else current_y
        else:
            current.sort(key=lambda c: c[0])
            lines.append(current)
            current = [(x0, y0, x1, y1, text)]
            current_y = y0

    if current:
        current.sort(key=lambda c: c[0])
        lines.append(current)

    return lines


def _line_column_boundaries(
    line: list[tuple[float, float, float, float, str]],
) -> list[float]:
    """
    Return the x-positions of gaps >= _MIN_COLUMN_GAP_PT between
    consecutive words on a line -- i.e. candidate column boundaries.
    """
    boundaries: list[float] = []
    for prev, cur in zip(line, line[1:]):
        gap = cur[0] - prev[2]
        if gap >= _MIN_COLUMN_GAP_PT:
            boundaries.append((prev[2] + cur[0]) / 2.0)
    return boundaries


def _boundaries_align(a: list[float], b: list[float]) -> bool:
    """True if two boundary-position lists match within tolerance."""
    if len(a) != len(b):
        return False
    return all(abs(x - y) <= _COLUMN_ALIGN_TOLERANCE_PT for x, y in zip(a, b))


def _split_line_into_cells(
    line: list[tuple[float, float, float, float, str]],
    boundaries: list[float],
) -> list[str]:
    """Split a line's words into cell strings using column boundary x-positions."""
    if not boundaries:
        return [" ".join(w[4] for w in line)]

    cells: list[list[str]] = [[] for _ in range(len(boundaries) + 1)]
    for x0, y0, x1, y1, text in line:
        col = 0
        for i, b in enumerate(boundaries):
            if x0 >= b:
                col = i + 1
            else:
                break
        cells[col].append(text)

    return [" ".join(c).strip() for c in cells]


def _detect_borderless_tables(page: fitz.Page) -> list[dict[str, Any]]:
    """
    Fallback table detector for whitespace-aligned tables with no drawn
    borders, which PyMuPDF's `find_tables()` does not detect at all.

    Only fires when at least `_MIN_TABLE_ROWS` consecutive lines share the
    same column-boundary structure, so ordinary prose (including justified
    two-column layouts, which have gaps but not *consistent, repeating*
    gaps across many rows) is not misclassified as a table.
    """
    try:
        words = page.get_text("words")
    except Exception:
        return []

    lines = _group_words_into_lines(words)
    if len(lines) < _MIN_TABLE_ROWS:
        return []

    results: list[dict[str, Any]] = []
    run: list[list[tuple[float, float, float, float, str]]] = []
    run_boundaries: list[float] | None = None

    def _flush_run():
        if run and len(run) >= _MIN_TABLE_ROWS and run_boundaries:
            rows = [_split_line_into_cells(ln, run_boundaries) for ln in run]
            x0 = min(w[0] for ln in run for w in ln)
            y0 = min(w[1] for ln in run for w in ln)
            x1 = max(w[2] for ln in run for w in ln)
            y1 = max(w[3] for ln in run for w in ln)
            bbox = (x0, y0, x1, y1)
            dominant_font = _dominant_font_in_bbox(page, bbox)
            converted_rows = [
                [_convert_cell(_clean_cell(cell), dominant_font) for cell in row]
                for row in rows
            ]
            results.append({
                "bbox": bbox,
                "rows": converted_rows,
                "formatted": _draw_table(converted_rows),
                "detected_by": "column_clustering",
            })

    for line in lines:
        boundaries = _line_column_boundaries(line)
        # A "table row" needs at least one internal gap (i.e. >= 2 columns).
        if not boundaries:
            _flush_run()
            run, run_boundaries = [], None
            continue

        if run_boundaries is None or _boundaries_align(boundaries, run_boundaries):
            run.append(line)
            # Keep the boundary set from the first row of the run; later
            # rows just need to align with it within tolerance.
            run_boundaries = run_boundaries or boundaries
        else:
            _flush_run()
            run, run_boundaries = [line], boundaries

    _flush_run()
    return results


def extract_tables_from_page(
    page: fitz.Page,
    font_lookup: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """
    Detect and extract all tables from a PDF page.

    Tries PyMuPDF's ruling-line table detector first; if that finds
    nothing, falls back to whitespace/column-clustering detection so
    borderless tables (common in Nepali government/financial documents)
    are not silently dropped into flat paragraph text.

    Returns a list of dicts:
        {
            "bbox": (x0, y0, x1, y1),        # table bounding box
            "rows": [[str, ...], ...],       # 2-D list of cell text
            "formatted": str,                # ready-to-print box table
            "detected_by": str,              # "ruling_lines" | "column_clustering"
        }
    """
    results: list[dict[str, Any]] = []

    try:
        tabs = page.find_tables()
    except Exception:
        tabs = None
        logger_warn = True
    else:
        logger_warn = False

    if tabs and tabs.tables:
        for tab in tabs.tables:
            converted = _convert_ruling_line_table(page, tab)
            if converted:
                results.append(converted)

    if not results:
        # Either find_tables() found nothing, or it raised. Either way,
        # try the borderless fallback rather than silently losing the
        # table (this is the behavior change from earlier versions of
        # this module, which returned nothing on both of these paths).
        results.extend(_detect_borderless_tables(page))

    return results


def get_table_bboxes(page: fitz.Page) -> list[tuple[float, float, float, float]]:
    """
    Return the bounding boxes of all tables on the page (both ruling-line
    and borderless/column-clustering detected).
    Used by the text extractor to SKIP table regions (so they are not
    double-extracted as raw text).
    """
    bboxes: list[tuple[float, float, float, float]] = []
    try:
        tabs = page.find_tables()
        if tabs and tabs.tables:
            bboxes = [t.bbox for t in tabs.tables]
    except Exception:
        pass

    if not bboxes:
        bboxes = [t["bbox"] for t in _detect_borderless_tables(page)]

    return bboxes
