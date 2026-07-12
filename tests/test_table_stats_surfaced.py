"""
Regression test: table detection stats (count + detection method
breakdown) must be aggregated onto the top-level result of
extract_document_high_accuracy(), not just left buried in per-page
results.

Added per the UI/feature audit recommendation that users should be able
to see whether the app detected any tables in their document (and via
which method -- ruling lines vs the borderless-table column-clustering
fallback), rather than tables being silently present-or-absent with no
visibility into the process.
"""
from __future__ import annotations

import fitz

from app.extract.direct_extract import extract_document_high_accuracy


def _build_borderless_table_pdf_bytes():
    doc = fitz.open()
    page = doc.new_page(width=400, height=300)
    page.insert_text((40, 40), "Header text about the report")
    rows = [("Item", "Qty", "Price"), ("Rice", "10", "500"), ("Oil", "5", "750"), ("Sugar", "3", "150")]
    y = 80
    for row in rows:
        page.insert_text((40, y), row[0])
        page.insert_text((150, y), row[1])
        page.insert_text((230, y), row[2])
        y += 20
    data = doc.tobytes()
    doc.close()
    return data


def test_tables_detected_and_method_breakdown_present_on_result():
    pdf_bytes = _build_borderless_table_pdf_bytes()
    result = extract_document_high_accuracy(pdf_bytes)

    assert "tables_detected" in result
    assert "tables_by_method" in result
    assert result["tables_detected"] == 1
    assert result["tables_by_method"] == {"column_clustering": 1}


def test_no_tables_gives_zero_count():
    doc = fitz.open()
    page = doc.new_page(width=400, height=300)
    page.insert_text((40, 40), "Just an ordinary paragraph of prose text.")
    page.insert_text((40, 60), "No tabular structure anywhere on this page.")
    pdf_bytes = doc.tobytes()
    doc.close()

    result = extract_document_high_accuracy(pdf_bytes)
    assert result["tables_detected"] == 0
    assert result["tables_by_method"] == {}
