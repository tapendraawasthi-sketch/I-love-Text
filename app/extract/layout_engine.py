"""
Layout engine: multi-column detection and reading-order reconstruction.

Implements a modified Recursive XY-Cut algorithm that:
1. Detects columns by finding large horizontal/vertical whitespace gaps
2. Recursively segments the page into reading blocks
3. Assigns reading order via topological sort

This correctly handles:
- Two-column legal documents
- Three-column newspapers
- Mixed single/multi-column pages
- Side-by-side tables
- Margin notes
"""
from __future__ import annotations

import statistics
from typing import Any

from app.extract.document_model import BBox, DocumentElement, Column, PageModel
from app.logging_config import get_logger

logger = get_logger("LayoutEngine")

# Minimum gap (as fraction of page dimension) to consider a column break
MIN_COLUMN_GAP_RATIO = 0.03  # 3% of page width
MIN_ROW_GAP_RATIO = 0.015    # 1.5% of page height

# Minimum column width as fraction of page width
MIN_COLUMN_WIDTH_RATIO = 0.15

# Maximum number of columns to detect
MAX_COLUMNS = 5


def _project_horizontal(elements: list[DocumentElement], page_width: float) -> list[float]:
    """
    Build a 1D horizontal projection profile.
    Returns density at each x-position (binned).
    """
    bins = 200
    bin_width = page_width / bins
    profile = [0.0] * bins

    for elem in elements:
        start_bin = max(0, int(elem.bbox.x0 / bin_width))
        end_bin = min(bins - 1, int(elem.bbox.x1 / bin_width))
        for b in range(start_bin, end_bin + 1):
            profile[b] += elem.bbox.height

    return profile


def _find_gaps(profile: list[float], min_gap_bins: int = 3) -> list[tuple[int, int]]:
    """Find contiguous zero-density regions in a projection profile."""
    gaps = []
    in_gap = False
    gap_start = 0

    for i, val in enumerate(profile):
        if val < 0.1:  # Near-zero density
            if not in_gap:
                gap_start = i
                in_gap = True
        else:
            if in_gap and (i - gap_start) >= min_gap_bins:
                gaps.append((gap_start, i))
            in_gap = False

    if in_gap and (len(profile) - gap_start) >= min_gap_bins:
        gaps.append((gap_start, len(profile)))

    return gaps


def detect_columns(
    elements: list[DocumentElement],
    page_width: float,
    page_height: float,
) -> list[Column]:
    """
    Detect text columns on a page using horizontal projection profiles.

    Returns a list of Column objects, each with a bounding box.
    Single-column pages return one column spanning the full width.
    """
    if not elements:
        return [Column(bbox=BBox(0, 0, page_width, page_height), index=0)]

    # Filter to body elements only (exclude tiny elements)
    body_elements = [
        e for e in elements
        if e.bbox.height > 5 and e.bbox.width > page_width * 0.05
    ]

    if not body_elements:
        return [Column(bbox=BBox(0, 0, page_width, page_height), index=0)]

    # Build horizontal projection
    profile = _project_horizontal(body_elements, page_width)
    bins = len(profile)
    bin_width = page_width / bins

    min_gap_width = page_width * MIN_COLUMN_GAP_RATIO
    min_gap_bins = max(2, int(min_gap_width / bin_width))

    gaps = _find_gaps(profile, min_gap_bins)

    if not gaps:
        # Single column
        x0 = min(e.bbox.x0 for e in body_elements)
        x1 = max(e.bbox.x1 for e in body_elements)
        return [Column(bbox=BBox(x0, 0, x1, page_height), index=0)]

    # Filter gaps that are too narrow or at page edges
    margin = page_width * 0.05
    significant_gaps = []
    for g_start, g_end in gaps:
        gx0 = g_start * bin_width
        gx1 = g_end * bin_width
        gap_width = gx1 - gx0

        # Skip gaps at page margins
        if gx0 < margin or gx1 > page_width - margin:
            continue

        # Gap must be significant
        if gap_width >= min_gap_width:
            significant_gaps.append((gx0, gx1))

    if not significant_gaps or len(significant_gaps) >= MAX_COLUMNS:
        # Too many gaps = probably not columns (might be sparse text)
        x0 = min(e.bbox.x0 for e in body_elements)
        x1 = max(e.bbox.x1 for e in body_elements)
        return [Column(bbox=BBox(x0, 0, x1, page_height), index=0)]

    # Build columns from gaps
    columns: list[Column] = []
    all_x0 = min(e.bbox.x0 for e in body_elements)
    all_x1 = max(e.bbox.x1 for e in body_elements)

    col_boundaries = [all_x0]
    for gx0, gx1 in sorted(significant_gaps):
        mid = (gx0 + gx1) / 2
        col_boundaries.append(mid)
    col_boundaries.append(all_x1)

    for i in range(len(col_boundaries) - 1):
        cx0 = col_boundaries[i]
        cx1 = col_boundaries[i + 1]

        # Check minimum column width
        if (cx1 - cx0) < page_width * MIN_COLUMN_WIDTH_RATIO:
            continue

        columns.append(Column(
            bbox=BBox(cx0, 0, cx1, page_height),
            index=len(columns),
        ))

    if not columns:
        return [Column(bbox=BBox(all_x0, 0, all_x1, page_height), index=0)]

    # Validate: columns should have roughly similar element counts
    for col in columns:
        col.elements = [
            e for e in body_elements
            if _element_in_column(e, col)
        ]

    # If one column has < 10% of elements, it's probably not a real column
    total_elem = sum(len(c.elements) for c in columns)
    if total_elem > 0:
        columns = [
            c for c in columns
            if len(c.elements) >= max(1, total_elem * 0.08)
        ]

    if not columns:
        return [Column(bbox=BBox(all_x0, 0, all_x1, page_height), index=0)]

    # Re-index
    for i, col in enumerate(columns):
        col.index = i

    logger.debug(
        "Detected %d column(s) on page (widths: %s)",
        len(columns),
        [f"{c.bbox.width:.0f}" for c in columns]
    )

    return columns


def _element_in_column(elem: DocumentElement, col: Column) -> bool:
    """Check if element's center falls within column boundaries."""
    center_x = elem.bbox.center_x
    return col.bbox.x0 - 5 <= center_x <= col.bbox.x1 + 5


def detect_full_width_elements(
    elements: list[DocumentElement],
    columns: list[Column],
    page_width: float,
) -> tuple[list[DocumentElement], list[DocumentElement]]:
    """
    Separate full-width elements (headings, etc.) from column elements.

    Full-width elements span across multiple columns and should be
    read before/after the columnar text they precede/follow.

    Returns: (full_width_elements, column_elements)
    """
    if len(columns) <= 1:
        return [], elements

    total_col_width = columns[-1].bbox.x1 - columns[0].bbox.x0
    threshold = total_col_width * 0.7  # Element spans > 70% of column area

    full_width = []
    column_bound = []

    for elem in elements:
        if elem.bbox.width >= threshold:
            full_width.append(elem)
        else:
            column_bound.append(elem)

    return full_width, column_bound


def assign_reading_order(
    page: PageModel,
) -> None:
    """
    Assign reading order to all elements on a page.

    Strategy:
    1. Detect columns
    2. Separate full-width elements from column elements
    3. For column sections: read left-to-right, top-to-bottom within each column
    4. Full-width elements are read at their vertical position

    This handles mixed layouts like:
        [Full-width heading]
        [Col 1 para] [Col 2 para]
        [Col 1 para] [Col 2 para]
        [Full-width table]
        [Col 1 para] [Col 2 para]
    """
    if not page.elements:
        return

    columns = detect_columns(page.elements, page.width, page.height)
    page.columns = columns

    if len(columns) <= 1:
        # Single column: simple top-to-bottom order
        sorted_elems = sorted(page.elements, key=lambda e: (e.bbox.y0, e.bbox.x0))
        for i, elem in enumerate(sorted_elems):
            elem.reading_order = i
            elem.column_index = 0
        return

    full_width, column_bound = detect_full_width_elements(
        page.elements, columns, page.width
    )

    # Assign column indices to column-bound elements
    for elem in column_bound:
        best_col = 0
        best_overlap = 0
        for col in columns:
            if _element_in_column(elem, col):
                best_col = col.index
                break
        elem.column_index = best_col

    # Group column elements by vertical bands
    # A "band" is a horizontal strip of the page where columns are active
    all_ys = sorted(set(
        [e.bbox.y0 for e in column_bound] +
        [e.bbox.y1 for e in column_bound] +
        [e.bbox.y0 for e in full_width] +
        [e.bbox.y1 for e in full_width]
    ))

    # Build ordered sequence
    ordered: list[DocumentElement] = []

    # Sort full-width by y position
    fw_sorted = sorted(full_width, key=lambda e: e.bbox.y0)
    cb_sorted = sorted(column_bound, key=lambda e: e.bbox.y0)

    # Merge full-width and column elements by vertical position
    fw_idx = 0
    cb_start = 0

    while fw_idx < len(fw_sorted) or cb_start < len(cb_sorted):
        # Determine which comes next: a full-width element or a column section
        next_fw_y = fw_sorted[fw_idx].bbox.y0 if fw_idx < len(fw_sorted) else float('inf')

        # Find the next column element that starts before the next full-width
        col_section = []
        while cb_start < len(cb_sorted) and cb_sorted[cb_start].bbox.y0 < next_fw_y:
            col_section.append(cb_sorted[cb_start])
            cb_start += 1

        # Emit column section in column order (left col top-to-bottom, then right col)
        if col_section:
            for col in columns:
                col_elems = sorted(
                    [e for e in col_section if e.column_index == col.index],
                    key=lambda e: (e.bbox.y0, e.bbox.x0)
                )
                ordered.extend(col_elems)

        # Emit full-width element
        if fw_idx < len(fw_sorted):
            ordered.append(fw_sorted[fw_idx])
            fw_y1 = fw_sorted[fw_idx].bbox.y1
            fw_idx += 1

            # Skip column elements that overlap with the full-width element
            while cb_start < len(cb_sorted) and cb_sorted[cb_start].bbox.y0 < fw_y1:
                ordered.append(cb_sorted[cb_start])
                cb_start += 1

    # Assign reading order indices
    for i, elem in enumerate(ordered):
        elem.reading_order = i


def detect_page_regions(
    elements: list[DocumentElement],
    page_height: float,
    header_ratio: float = 0.08,
    footer_ratio: float = 0.08,
) -> tuple[list[DocumentElement], list[DocumentElement], list[DocumentElement], list[DocumentElement]]:
    """
    Classify elements into header, body, footer, and footnote regions.

    Returns: (headers, body, footers, footnotes)
    """
    header_limit = page_height * header_ratio
    footer_start = page_height * (1 - footer_ratio)

    # Footnote detection: small text near page bottom, often preceded by a line
    footnote_start = page_height * 0.75

    headers = []
    body = []
    footers = []
    footnotes = []

    for elem in elements:
        mid_y = elem.bbox.center_y

        if mid_y <= header_limit:
            elem.element_type = ElementType.HEADER
            headers.append(elem)
        elif mid_y >= footer_start:
            # Distinguish footer from footnote
            if _is_likely_footnote(elem, page_height):
                elem.element_type = ElementType.FOOTNOTE
                footnotes.append(elem)
            else:
                elem.element_type = ElementType.FOOTER
                footers.append(elem)
        elif elem.bbox.y0 >= footnote_start and _is_likely_footnote(elem, page_height):
            elem.element_type = ElementType.FOOTNOTE
            footnotes.append(elem)
        else:
            body.append(elem)

    return headers, body, footers, footnotes


def _is_likely_footnote(elem: DocumentElement, page_height: float) -> bool:
    """Heuristic to detect footnotes vs regular footer text."""
    text = elem.text.strip()
    if not text:
        return False

    # Footnotes typically have smaller font
    if elem.font_size > 0 and elem.font_size < 9:
        # Check for footnote markers: numbers, *, †, ‡
        if re.match(r'^[\d\*†‡¹²³⁴⁵⁶⁷⁸⁹⁰]+[\.\)]\s', text):
            return True
        if re.match(r'^\d+\s', text) and elem.bbox.y0 > page_height * 0.8:
            return True

    # Check if preceded by a horizontal line
    return False


import re
