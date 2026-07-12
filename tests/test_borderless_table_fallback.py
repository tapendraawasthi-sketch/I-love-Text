"""
Functional test for the borderless/whitespace-aligned table fallback.

PyMuPDF's `page.find_tables()` only detects tables that have drawn ruling
lines. A large share of real Nepali government/financial documents use
whitespace-aligned columns with no borders at all, which `find_tables()`
silently returns nothing for -- previously causing table content to fall
through to plain paragraph text with columns collapsed. This test builds a
synthetic borderless table with PyMuPDF and checks the fallback detector
in app.extract.table_extractor picks it up correctly.
"""
from __future__ import annotations

import fitz
import pytest

from app.extract.table_extractor import extract_tables_from_page, get_table_bboxes


def _build_borderless_table_pdf(tmp_path):
    doc = fitz.open()
    page = doc.new_page(width=400, height=300)
    rows = [
        ("Item", "Qty", "Price"),
        ("Rice", "10", "500"),
        ("Oil", "5", "750"),
        ("Sugar", "3", "150"),
    ]
    y = 40
    for row in rows:
        page.insert_text((40, y), row[0])
        page.insert_text((150, y), row[1])
        page.insert_text((230, y), row[2])
        y += 20
    path = tmp_path / "borderless_table.pdf"
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture
def borderless_table_page(tmp_path):
    path = _build_borderless_table_pdf(tmp_path)
    doc = fitz.open(str(path))
    page = doc.load_page(0)
    yield page
    doc.close()


def test_ruling_line_detector_finds_nothing_on_borderless_table(borderless_table_page):
    # Sanity check: this synthetic document has no drawn borders, so
    # PyMuPDF's built-in detector should find nothing (confirms the test
    # is actually exercising the fallback path, not the primary one).
    tabs = borderless_table_page.find_tables()
    assert not tabs or not tabs.tables


def test_fallback_detects_borderless_table(borderless_table_page):
    results = extract_tables_from_page(borderless_table_page)
    assert len(results) == 1

    table = results[0]
    assert table["detected_by"] == "column_clustering"

    rows = table["rows"]
    assert len(rows) == 4
    assert rows[0] == ["Item", "Qty", "Price"]
    assert rows[1] == ["Rice", "10", "500"]
    assert rows[2] == ["Oil", "5", "750"]
    assert rows[3] == ["Sugar", "3", "150"]


def test_get_table_bboxes_includes_fallback_table(borderless_table_page):
    bboxes = get_table_bboxes(borderless_table_page)
    assert len(bboxes) == 1


def test_no_false_positive_on_plain_prose(tmp_path):
    """
    Ordinary prose with occasional wide gaps (e.g. justified text) should
    NOT be detected as a table -- the fallback requires several
    consecutive rows with *matching* column structure.
    """
    doc = fitz.open()
    page = doc.new_page(width=400, height=300)
    lines = [
        "This is an ordinary paragraph of text.",
        "It has no tabular structure at all.",
        "Just normal prose spanning the page width.",
    ]
    y = 40
    for line in lines:
        page.insert_text((40, y), line)
        y += 20
    path = tmp_path / "prose.pdf"
    doc.save(str(path))
    doc.close()

    doc2 = fitz.open(str(path))
    page2 = doc2.load_page(0)
    results = extract_tables_from_page(page2)
    assert results == []
    doc2.close()
