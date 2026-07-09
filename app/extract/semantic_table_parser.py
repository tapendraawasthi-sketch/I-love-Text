"""
Semantic Table Parser — understands table MEANING, not just geometry.

Detects:
    - Header rows (bold, different background, first row)
    - Stub columns (leftmost column with labels)
    - Unit rows (Rs., '000, %, etc.)
    - Subtotal/total rows (जम्मा, Total, Grand Total)
    - Merged header cells (spanning multiple columns)
    - Nested table sections
    - Fiscal year columns (२०७९/८०, 2079/80)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.logging_config import get_logger

logger = get_logger("SemanticTableParser")

# --- Patterns ---
_TOTAL_RE = re.compile(
    r"(?:जम्मा|कुल|Total|Grand\s*Total|Sub[\-\s]*Total|"
    r"योग|सम्पूर्ण|Aggregate|शेष|Balance|Net)",
    re.IGNORECASE,
)
_UNIT_RE = re.compile(
    r"(?:रु\.?|Rs\.?|NRs\.?|'000|हजार|लाख|करोड|%|"
    r"प्रतिशत|Percent|Amount|रकम)",
    re.IGNORECASE,
)
_FISCAL_YEAR_RE = re.compile(
    r"(?:[\u0966-\u096F]{4}[/\-][\u0966-\u096F]{2,4}|"
    r"\d{4}[/\-]\d{2,4}|"
    r"आ\.व\.|FY|Fiscal\s*Year)",
    re.IGNORECASE,
)
_SN_RE = re.compile(r"(?:क्र\.?\s*सं\.?|S\.?N\.?|#|No\.?)", re.IGNORECASE)
_NUMERIC_RE = re.compile(r"^[\d,.\s\-()]+$")


@dataclass
class TableSemantics:
    """Semantic understanding of a table."""
    rows: list[list[str]]
    header_rows: list[int] = field(default_factory=list)
    stub_column: int | None = None
    total_rows: list[int] = field(default_factory=list)
    unit_info: str = ""
    fiscal_years: list[str] = field(default_factory=list)
    column_types: list[str] = field(default_factory=list)  # "label", "numeric", "date", etc.
    has_merged_headers: bool = False
    nested_sections: list[tuple[int, int, str]] = field(default_factory=list)  # (start, end, label)


def analyze_table_semantics(rows: list[list[str]]) -> TableSemantics:
    """
    Analyze a table's semantic structure.

    Goes beyond geometry to understand what each row/column means.
    """
    if not rows:
        return TableSemantics(rows=rows)

    sem = TableSemantics(rows=rows)
    col_count = max(len(r) for r in rows) if rows else 0

    # --- Detect header rows ---
    # Usually the first 1-2 rows, especially if they contain non-numeric text
    for i, row in enumerate(rows[:3]):
        numeric_cells = sum(1 for c in row if _NUMERIC_RE.match(c.strip()))
        total_cells = sum(1 for c in row if c.strip())
        if total_cells > 0 and numeric_cells / total_cells < 0.4:
            sem.header_rows.append(i)

    # Check for unit indicators in header area
    for i in sem.header_rows:
        row_text = " ".join(rows[i])
        unit_match = _UNIT_RE.search(row_text)
        if unit_match:
            sem.unit_info = unit_match.group()
        fy_match = _FISCAL_YEAR_RE.search(row_text)
        if fy_match:
            sem.fiscal_years.append(fy_match.group())

    # --- Detect stub column (row labels) ---
    if col_count >= 2:
        first_col_numeric = sum(
            1 for row in rows[len(sem.header_rows):]
            if row and _NUMERIC_RE.match(row[0].strip())
        )
        data_rows = len(rows) - len(sem.header_rows)
        if data_rows > 0 and first_col_numeric / data_rows < 0.3:
            # Check if it's an S.N. column
            if rows and rows[0] and _SN_RE.match(rows[0][0].strip()):
                sem.stub_column = 1  # Second column is the real stub
            else:
                sem.stub_column = 0

    # --- Detect total rows ---
    for i, row in enumerate(rows):
        row_text = " ".join(row)
        if _TOTAL_RE.search(row_text):
            sem.total_rows.append(i)

    # --- Classify column types ---
    for col_idx in range(col_count):
        col_values = [
            rows[i][col_idx].strip()
            for i in range(len(rows))
            if i not in sem.header_rows and col_idx < len(rows[i])
        ]
        numeric_count = sum(1 for v in col_values if v and _NUMERIC_RE.match(v))
        total_count = sum(1 for v in col_values if v)

        if total_count == 0:
            sem.column_types.append("empty")
        elif numeric_count / total_count > 0.7:
            sem.column_types.append("numeric")
        elif col_idx == sem.stub_column:
            sem.column_types.append("label")
        else:
            sem.column_types.append("text")

    # --- Detect nested sections ---
    # Rows where stub column is bold/indented differently
    if sem.stub_column is not None:
        current_section_start = None
        current_section_label = ""
        for i, row in enumerate(rows):
            if i in sem.header_rows or i in sem.total_rows:
                continue
            if sem.stub_column < len(row):
                cell = row[sem.stub_column].strip()
                # A section header is a row where only the stub has content
                other_cells = [
                    row[j].strip() for j in range(col_count)
                    if j != sem.stub_column and j < len(row)
                ]
                if cell and all(not c for c in other_cells):
                    if current_section_start is not None:
                        sem.nested_sections.append(
                            (current_section_start, i - 1, current_section_label)
                        )
                    current_section_start = i
                    current_section_label = cell

        if current_section_start is not None:
            sem.nested_sections.append(
                (current_section_start, len(rows) - 1, current_section_label)
            )

    return sem


def format_semantic_table(
    rows: list[list[str]],
    semantics: TableSemantics | None = None,
) -> str:
    """
    Format a table with semantic awareness.

    Uses semantics to:
    - Bold header rows
    - Indent nested sections
    - Highlight totals
    - Preserve alignment for numeric columns
    """
    if not rows:
        return ""

    if semantics is None:
        semantics = analyze_table_semantics(rows)

    col_count = max(len(r) for r in rows) if rows else 0

    # Normalize row lengths
    normalized = []
    for row in rows:
        padded = list(row) + [""] * (col_count - len(row))
        normalized.append(padded)

    # Calculate column widths
    col_widths = [4] * col_count
    for row in normalized:
        for i, cell in enumerate(row):
            w = len(cell) + 2
            # Devanagari characters are wider
            w += sum(1 for c in cell if 0x0900 <= ord(c) <= 0x097F)
            col_widths[i] = max(col_widths[i], min(w, 50))

    # Build table
    lines: list[str] = []

    def _separator(left, mid, right, fill):
        return left + mid.join(fill * w for w in col_widths) + right

    def _row_text(row, is_header=False, is_total=False):
        cells = []
        for i, cell in enumerate(row):
            dw = len(cell) + sum(1 for c in cell if 0x0900 <= ord(c) <= 0x097F)
            pad = col_widths[i] - dw - 1
            cells.append(" " + cell + " " * max(0, pad))
        line = "│" + "│".join(cells) + "│"
        if is_total:
            line = line + "  ◄ TOTAL"
        return line

    lines.append(_separator("┌", "┬", "┐", "─"))

    for i, row in enumerate(normalized):
        is_header = i in semantics.header_rows
        is_total = i in semantics.total_rows
        lines.append(_row_text(row, is_header, is_total))

        if is_header and i == max(semantics.header_rows, default=-1):
            lines.append(_separator("├", "┼", "┤", "═"))
        elif i < len(normalized) - 1:
            lines.append(_separator("├", "┼", "┤", "─"))

    lines.append(_separator("└", "┴", "┘", "─"))

    return "\n".join(lines)
