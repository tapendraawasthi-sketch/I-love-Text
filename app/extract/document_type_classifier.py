"""
Document Type Classifier — identifies the document class to select
the optimal extraction and serialization strategy.

Classes:
    - legal_act: Nepal Acts (ऐन), Rules (नियम), Finance Act
    - legal_circular: NRB circulars, government notices
    - financial_statement: Balance sheets, P&L, trial balance
    - audit_report: Auditor General reports, bank audit reports
    - book: Multi-chapter books, textbooks
    - newspaper: Multi-column newspaper layout
    - form: Government forms, applications
    - general: Catch-all for unclassified documents

Detection uses:
    1. Page 1 title text analysis
    2. Font distribution (Preeti = likely government)
    3. Layout patterns (multi-column, tables, numbering)
    4. Keyword detection (ऐन, नियम, अनुसूची, etc.)
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from enum import Enum
from typing import Any


class DocumentClass(Enum):
    LEGAL_ACT = "legal_act"
    LEGAL_CIRCULAR = "legal_circular"
    FINANCIAL_STATEMENT = "financial_statement"
    AUDIT_REPORT = "audit_report"
    BOOK = "book"
    NEWSPAPER = "newspaper"
    FORM = "form"
    GENERAL = "general"


@dataclass
class ClassificationResult:
    """Result of document classification."""
    primary_class: DocumentClass
    confidence: float
    secondary_class: DocumentClass | None = None
    evidence: list[str] = None

    def __post_init__(self):
        if self.evidence is None:
            self.evidence = []


# --- Keyword patterns ---

_LEGAL_ACT_RE = re.compile(
    r"(?:ऐन|नियम|नियमावली|विनियम|विधेयक|अध्यादेश|संविधान|"
    r"कानून|दफा|उपदफा|परिच्छेद|भाग|अनुसूची|"
    r"Act|Ordinance|Regulation|Rule|Bill|Constitution)",
    re.IGNORECASE,
)

_LEGAL_CIRCULAR_RE = re.compile(
    r"(?:परिपत्र|निर्देशन|आदेश|सूचना|Circular|Notice|Directive|"
    r"राष्ट्र\s*बैंक|नेपाल\s*राष्ट्र|NRB|Nepal\s*Rastra)",
    re.IGNORECASE,
)

_FINANCIAL_RE = re.compile(
    r"(?:वासलात|ब्यालेन्स\s*शीट|Balance\s*Sheet|"
    r"नाफा\s*नोक्सान|Profit.*Loss|Income\s*Statement|"
    r"नगद\s*प्रवाह|Cash\s*Flow|"
    r"लेखापरीक्षण|Audit|Trial\s*Balance|"
    r"वित्तीय\s*विवरण|Financial\s*Statement)",
    re.IGNORECASE,
)

_AUDIT_RE = re.compile(
    r"(?:महालेखापरीक्षक|Auditor\s*General|"
    r"लेखापरीक्षण\s*प्रतिवेदन|Audit\s*Report|"
    r"अन्तिम\s*लेखापरीक्षण|Final\s*Audit)",
    re.IGNORECASE,
)

_BOOK_INDICATORS = re.compile(
    r"(?:अध्याय|Chapter|विषयसूची|Table\s*of\s*Contents|"
    r"Index|सन्दर्भ|Bibliography|References|"
    r"ISBN|प्रकाशन|Publication|प्रकाशक|Publisher)",
    re.IGNORECASE,
)

_FORM_INDICATORS = re.compile(
    r"(?:निवेदन|Application|फारम|Form|"
    r"दरखास्त|भर्नुहोस|Fill\s*in|Signature|"
    r"हस्ताक्षर|सही|मिति|Date)",
    re.IGNORECASE,
)


def classify_document(
    first_pages_text: list[str],
    font_analysis: dict[str, Any] | None = None,
    page_count: int = 0,
    has_multi_column: bool = False,
) -> ClassificationResult:
    """
    Classify a document based on its content and structure.

    Args:
        first_pages_text: Text from first 3-5 pages
        font_analysis: Font detection results
        page_count: Total page count
        has_multi_column: Whether multi-column layout detected
    """
    combined_text = "\n".join(first_pages_text[:5])
    scores: dict[DocumentClass, float] = {c: 0.0 for c in DocumentClass}
    evidence: list[str] = []

    # --- Keyword scoring ---
    if _LEGAL_ACT_RE.search(combined_text):
        act_hits = len(_LEGAL_ACT_RE.findall(combined_text))
        scores[DocumentClass.LEGAL_ACT] += min(40, act_hits * 8)
        evidence.append(f"legal_act keywords: {act_hits}")

    if _LEGAL_CIRCULAR_RE.search(combined_text):
        scores[DocumentClass.LEGAL_CIRCULAR] += 35
        evidence.append("circular keywords found")

    if _FINANCIAL_RE.search(combined_text):
        scores[DocumentClass.FINANCIAL_STATEMENT] += 35
        evidence.append("financial keywords found")

    if _AUDIT_RE.search(combined_text):
        scores[DocumentClass.AUDIT_REPORT] += 35
        evidence.append("audit keywords found")

    if _BOOK_INDICATORS.search(combined_text):
        scores[DocumentClass.BOOK] += 30
        evidence.append("book indicators found")

    if _FORM_INDICATORS.search(combined_text):
        form_hits = len(_FORM_INDICATORS.findall(combined_text))
        scores[DocumentClass.FORM] += min(30, form_hits * 10)
        evidence.append(f"form indicators: {form_hits}")

    # --- Structural scoring ---
    if page_count > 50:
        scores[DocumentClass.LEGAL_ACT] += 10
        scores[DocumentClass.BOOK] += 10
        evidence.append(f"long document: {page_count} pages")

    if has_multi_column:
        scores[DocumentClass.NEWSPAPER] += 25
        evidence.append("multi-column layout")

    # --- Font-based scoring ---
    if font_analysis:
        dominant = font_analysis.get("dominant_family", "unknown")
        if dominant in ("preeti", "kantipur", "sagarmatha"):
            scores[DocumentClass.LEGAL_ACT] += 15
            scores[DocumentClass.LEGAL_CIRCULAR] += 10
            evidence.append(f"legacy font: {dominant}")

    # --- Determine winner ---
    best_class = max(scores, key=scores.get)
    best_score = scores[best_class]

    if best_score < 15:
        best_class = DocumentClass.GENERAL

    # Second best for reporting
    sorted_classes = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    secondary = sorted_classes[1][0] if len(sorted_classes) > 1 else None

    confidence = min(100, best_score + 10)

    return ClassificationResult(
        primary_class=best_class,
        confidence=confidence,
        secondary_class=secondary,
        evidence=evidence,
    )


# --- Serialization strategies per document class ---

SERIALIZATION_CONFIG: dict[DocumentClass, dict[str, Any]] = {
    DocumentClass.LEGAL_ACT: {
        "indent_sections": True,
        "preserve_numbering": True,
        "section_separators": True,
        "paragraph_spacing": 1,
        "heading_markers": True,
        "schedule_formatting": True,
    },
    DocumentClass.FINANCIAL_STATEMENT: {
        "table_emphasis": True,
        "preserve_alignment": True,
        "numeric_formatting": True,
        "paragraph_spacing": 1,
        "heading_markers": True,
    },
    DocumentClass.BOOK: {
        "chapter_separators": True,
        "heading_markers": True,
        "paragraph_spacing": 2,
        "page_separators": True,
    },
    DocumentClass.NEWSPAPER: {
        "column_ordering": True,
        "article_separators": True,
        "paragraph_spacing": 1,
    },
    DocumentClass.GENERAL: {
        "heading_markers": True,
        "paragraph_spacing": 1,
        "page_separators": True,
    },
}
