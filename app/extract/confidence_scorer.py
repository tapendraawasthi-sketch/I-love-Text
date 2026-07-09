"""
Multi-dimensional confidence scoring for extraction results.

Every page produces five independent confidence scores:
    - extraction_confidence: Was all text captured?
    - layout_confidence: Is reading order correct?
    - table_confidence: Were tables correctly reconstructed?
    - encoding_confidence: Was font conversion accurate?
    - overall_confidence: Weighted combination

This lets consumers know which pages need human review.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.extract.document_model import (
    PageModel, DocumentModel, DocumentElement, ElementType
)
from app.extract.unicode_validator import validate_text_block

_DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]")
_GARBAGE_RE = re.compile(r"undefined|NaN|\[object|function\s*\(", re.I)
_PUA_RE = re.compile(r"[\uE000-\uF8FF]")


@dataclass
class PageConfidence:
    """Confidence scores for a single page."""
    page_number: int
    extraction: float = 0.0    # 0-100: Was all text captured?
    layout: float = 0.0        # 0-100: Is reading order correct?
    table: float = 0.0         # 0-100: Table reconstruction quality
    encoding: float = 0.0      # 0-100: Font conversion accuracy
    overall: float = 0.0       # 0-100: Weighted combination
    warnings: list[str] = None

    def __post_init__(self):
        if self.warnings is None:
            self.warnings = []

    def compute_overall(self) -> None:
        """Compute weighted overall score."""
        weights = {
            'extraction': 0.35,
            'layout': 0.25,
            'encoding': 0.25,
            'table': 0.15,
        }
        self.overall = (
            self.extraction * weights['extraction'] +
            self.layout * weights['layout'] +
            self.encoding * weights['encoding'] +
            self.table * weights['table']
        )


def score_page(page: PageModel) -> PageConfidence:
    """Compute confidence scores for a page with Unicode validation."""
    pc = PageConfidence(page_number=page.page_number)
    all_text = " ".join(e.text for e in page.all_elements_ordered)

    # --- Extraction confidence (existing) ---
    if not all_text.strip():
        pc.extraction = 0.0
        pc.warnings.append("No text extracted")
    else:
        score = 100.0
        if _GARBAGE_RE.search(all_text):
            score -= 30
            pc.warnings.append("Garbage patterns detected")
        pua_count = len(_PUA_RE.findall(all_text))
        if pua_count > 0:
            score -= min(20, pua_count * 2)
            pc.warnings.append(f"{pua_count} unconverted characters")
        char_count = len(all_text.strip())
        if char_count < 10:
            score -= 20
            pc.warnings.append("Very little text found")
        pc.extraction = max(0, min(100, score))

    # --- Unicode quality (NEW) ---
    unicode_quality = validate_text_block(all_text)
    if unicode_quality.get("repairs", 0) > 0:
        repair_ratio = unicode_quality.get("repair_ratio", 0)
        if repair_ratio > 20:
            pc.encoding -= 15
            pc.warnings.append(f"High Unicode repair ratio: {repair_ratio}%")
        elif repair_ratio > 5:
            pc.encoding -= 5
            pc.warnings.append(f"Some Unicode repairs needed: {repair_ratio}%")

    # --- Layout confidence (existing) ---
    layout_score = 85.0
    if len(page.columns) > 1:
        col_counts = [len(c.elements) for c in page.columns]
        if col_counts:
            avg = sum(col_counts) / len(col_counts)
            if avg > 0:
                variance = sum((c - avg) ** 2 for c in col_counts) / len(col_counts)
                if variance > avg * 2:
                    layout_score -= 15
                    pc.warnings.append("Unbalanced column content")
        layout_score = min(95, layout_score)
    else:
        layout_score = 95.0
    pc.layout = max(0, min(100, layout_score))

    # --- Table confidence (existing) ---
    tables = [e for e in page.elements if e.element_type == ElementType.TABLE]
    if not tables:
        pc.table = 100.0
    else:
        table_score = 80.0
        for table in tables:
            if not table.table_rows:
                table_score -= 20
                pc.warnings.append("Empty table detected")
            else:
                col_counts = [len(r) for r in table.table_rows]
                if len(set(col_counts)) > 2:
                    table_score -= 10
                    pc.warnings.append("Inconsistent table column counts")
                empty_rows = sum(
                    1 for r in table.table_rows
                    if all(not cell.strip() for cell in r)
                )
                if empty_rows > len(table.table_rows) * 0.3:
                    table_score -= 15
        pc.table = max(0, min(100, table_score))

    # --- Encoding confidence with Unicode validation (ENHANCED) ---
    if not all_text.strip():
        pc.encoding = 0.0
    else:
        enc_score = 100.0
        total_chars = sum(1 for c in all_text if c.strip())
        if total_chars > 0:
            deva_chars = sum(1 for c in all_text if _DEVANAGARI_RE.match(c))
            deva_ratio = deva_chars / total_chars

            if page.legacy_fonts_detected and deva_ratio < 0.2:
                enc_score -= 30
                pc.warnings.append("Low Devanagari ratio despite legacy fonts")

            # Unicode sequence quality
            if unicode_quality.get("confidence", 100) < 70:
                enc_score -= 20
                pc.warnings.append(
                    f"Unicode quality: {unicode_quality.get('confidence', 0)}%"
                )

        pc.encoding = max(0, min(100, enc_score))

    pc.compute_overall()
    return pc


def score_document(doc: DocumentModel) -> dict[str, Any]:
    """
    Compute confidence scores for the entire document.
    
    Returns a summary dict with per-page and aggregate scores.
    """
    page_scores = []
    for page in doc.pages:
        pc = score_page(page)
        page.confidence = pc.overall
        page.warnings = pc.warnings
        page_scores.append(pc)

    if not page_scores:
        return {"overall": 0.0, "pages": []}

    avg_overall = sum(pc.overall for pc in page_scores) / len(page_scores)
    min_overall = min(pc.overall for pc in page_scores)
    avg_extraction = sum(pc.extraction for pc in page_scores) / len(page_scores)
    avg_layout = sum(pc.layout for pc in page_scores) / len(page_scores)
    avg_encoding = sum(pc.encoding for pc in page_scores) / len(page_scores)
    avg_table = sum(pc.table for pc in page_scores) / len(page_scores)

    low_confidence_pages = [
        pc.page_number for pc in page_scores if pc.overall < 60
    ]

    doc.overall_confidence = avg_overall
    doc.validation_report = {
        "overall": round(avg_overall, 1),
        "min_page_confidence": round(min_overall, 1),
        "extraction": round(avg_extraction, 1),
        "layout": round(avg_layout, 1),
        "encoding": round(avg_encoding, 1),
        "table": round(avg_table, 1),
        "low_confidence_pages": low_confidence_pages,
        "total_warnings": sum(len(pc.warnings) for pc in page_scores),
    }

    return {
        "overall": round(avg_overall, 1),
        "per_page": [
            {
                "page": pc.page_number,
                "extraction": round(pc.extraction, 1),
                "layout": round(pc.layout, 1),
                "table": round(pc.table, 1),
                "encoding": round(pc.encoding, 1),
                "overall": round(pc.overall, 1),
                "warnings": pc.warnings,
            }
            for pc in page_scores
        ],
        "low_confidence_pages": low_confidence_pages,
    }
