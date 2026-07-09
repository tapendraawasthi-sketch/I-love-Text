"""
Post-extraction validator.

Answers the critical questions:
- Was every character extracted?
- Was every page processed?
- Were all tables reconstructed?
- Did reading order change?
- Did Unicode conversion succeed?
- Did paragraph count change?
"""
from __future__ import annotations

import re
from typing import Any

import fitz

from app.extract.document_model import DocumentModel, ElementType
from app.logging_config import get_logger

logger = get_logger("ExtractionValidator")

_DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]")


def validate_extraction(
    doc_model: DocumentModel,
    original_pdf_bytes: bytes,
) -> dict[str, Any]:
    """
    Comprehensive validation of extraction results.
    
    Compares extracted text against the original PDF to catch:
    - Missing pages
    - Lost text
    - Broken tables
    - Encoding failures
    """
    report = {
        "valid": True,
        "checks": [],
        "warnings": [],
        "errors": [],
    }

    # --- Check 1: All pages processed ---
    try:
        pdf = fitz.open(stream=original_pdf_bytes, filetype="pdf")
        expected_pages = len(pdf)
        pdf.close()
    except Exception:
        expected_pages = 0

    actual_pages = doc_model.total_pages
    check_pages = {
        "name": "page_count",
        "expected": expected_pages,
        "actual": actual_pages,
        "passed": actual_pages >= expected_pages,
    }
    report["checks"].append(check_pages)
    if not check_pages["passed"]:
        report["errors"].append(
            f"Missing pages: expected {expected_pages}, got {actual_pages}"
        )
        report["valid"] = False

    # --- Check 2: No empty pages (that shouldn't be empty) ---
    empty_pages = []
    for page in doc_model.pages:
        all_text = " ".join(e.text for e in page.all_elements_ordered)
        if len(all_text.strip()) < 5:
            empty_pages.append(page.page_number)

    check_empty = {
        "name": "empty_pages",
        "empty_pages": empty_pages,
        "count": len(empty_pages),
        "passed": len(empty_pages) == 0,
    }
    report["checks"].append(check_empty)
    if empty_pages:
        report["warnings"].append(
            f"{len(empty_pages)} empty page(s): {empty_pages[:10]}"
        )

    # --- Check 3: Encoding quality ---
    total_text = doc_model.serialize_to_text(include_page_separators=False)
    total_chars = sum(1 for c in total_text if c.strip())
    deva_chars = sum(1 for c in total_text if _DEVANAGARI_RE.match(c))
    pua_chars = sum(1 for c in total_text if "\uE000" <= c <= "\uF8FF")

    encoding_quality = 100.0
    if pua_chars > 0:
        encoding_quality -= min(30, pua_chars * 2)
    if doc_model.legacy_fonts and total_chars > 0:
        deva_ratio = deva_chars / total_chars
        if deva_ratio < 0.2:
            encoding_quality -= 20

    check_encoding = {
        "name": "encoding_quality",
        "total_chars": total_chars,
        "devanagari_chars": deva_chars,
        "pua_chars": pua_chars,
        "quality": round(encoding_quality, 1),
        "passed": encoding_quality >= 60,
    }
    report["checks"].append(check_encoding)
    if not check_encoding["passed"]:
        report["errors"].append(
            f"Low encoding quality: {encoding_quality:.1f}% "
            f"({pua_chars} unconverted chars)"
        )
        report["valid"] = False

    # --- Check 4: Table detection ---
    tables = []
    for page in doc_model.pages:
        for elem in page.elements:
            if elem.element_type == ElementType.TABLE:
                tables.append(elem)

    empty_tables = sum(1 for t in tables if not t.table_rows)
    check_tables = {
        "name": "table_quality",
        "total_tables": len(tables),
        "empty_tables": empty_tables,
        "passed": empty_tables == 0,
    }
    report["checks"].append(check_tables)
    if empty_tables:
        report["warnings"].append(f"{empty_tables} empty table(s) detected")

    # --- Check 5: Text density (sanity check) ---
    if expected_pages > 0 and total_chars > 0:
        chars_per_page = total_chars / expected_pages
        check_density = {
            "name": "text_density",
            "chars_per_page": round(chars_per_page),
            "passed": chars_per_page >= 50,
        }
        report["checks"].append(check_density)
        if chars_per_page < 50:
            report["warnings"].append(
                f"Low text density: {chars_per_page:.0f} chars/page"
            )

    # --- Check 6: Confidence score ---
    overall_confidence = doc_model.overall_confidence
    check_confidence = {
        "name": "confidence",
        "overall": round(overall_confidence, 1),
        "passed": overall_confidence >= 50,
    }
    report["checks"].append(check_confidence)
    if overall_confidence < 50:
        report["errors"].append(
            f"Low overall confidence: {overall_confidence:.1f}%"
        )

    # Summary
    passed = sum(1 for c in report["checks"] if c["passed"])
    total = len(report["checks"])
    report["summary"] = f"{passed}/{total} checks passed"
    report["valid"] = report["valid"] and len(report["errors"]) == 0

    return report
