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


from typing import Any

def score_multi_dimensional(
    text: str,
    page_number: int = 0,
    domain: str = "general",
) -> dict[str, Any]:
    """
    Produce multi-dimensional confidence scores.

    Returns confidence at character, word, line, paragraph, and page level.
    This addresses Problem 18 â€” human confidence zones.
    """
    import re

    if not text.strip():
        return {
            "page": 0,
            "character": 0,
            "word": 0,
            "line": 0,
            "paragraph": 0,
            "uncertain_regions": [],
        }

    # Character-level: check Unicode validity
    char_confidences = []
    for i, char in enumerate(text):
        if "\u0900" <= char <= "\u097F":
            # Devanagari character â€” check sequence validity
            prev = text[i-1] if i > 0 else ""
            from app.extract.glyph_model import is_valid_devanagari_sequence
            if is_valid_devanagari_sequence(prev, char):
                char_confidences.append(90.0)
            else:
                char_confidences.append(40.0)
        elif char.isascii() and char.isalpha():
            char_confidences.append(85.0)
        elif char.isspace() or char in "à¥¤,.;:!?()-":
            char_confidences.append(95.0)
        else:
            char_confidences.append(70.0)

    char_conf = sum(char_confidences) / max(len(char_confidences), 1)

    # Word-level: check against knowledge base
    try:
        from app.intelligence.nepal_knowledge_base import is_known_word
        words = re.findall(r"[\u0900-\u097F]+", text)
        known = sum(1 for w in words if is_known_word(w))
        word_conf = (known / max(len(words), 1)) * 100 if words else 80.0
    except ImportError:
        word_conf = 70.0

    # Line-level
    lines = text.split("\n")
    line_confs = []
    for line in lines:
        if not line.strip():
            continue
        line_chars = [c for c in line if c.strip()]
        if not line_chars:
            continue
        deva = sum(1 for c in line_chars if "\u0900" <= c <= "\u097F")
        ratio = deva / len(line_chars) if line_chars else 0
        # Lines with very low or very high Devanagari are more confident
        if ratio > 0.7 or ratio < 0.1:
            line_confs.append(85.0)
        else:
            line_confs.append(60.0)  # Mixed content â€” less certain

    line_conf = sum(line_confs) / max(len(line_confs), 1) if line_confs else 70.0

    # Paragraph-level
    paragraphs = text.split("\n\n")
    para_confs = []
    for para in paragraphs:
        if len(para.strip()) < 10:
            continue
        para_confs.append(line_conf)  # Simplified â€” real version would be more nuanced

    para_conf = sum(para_confs) / max(len(para_confs), 1) if para_confs else 70.0

    # Page-level
    page_conf = (char_conf * 0.2 + word_conf * 0.35 + line_conf * 0.25 + para_conf * 0.2)

    # Identify uncertain regions (low-confidence zones)
    uncertain_regions = []
    # Find words with low confidence
    for word in re.findall(r"[\u0900-\u097F]+", text):
        try:
            from app.intelligence.nepal_knowledge_base import is_known_word, find_closest_word
            if not is_known_word(word) and len(word) >= 3:
                matches = find_closest_word(word, max_distance=2)
                if not matches:
                    uncertain_regions.append({
                        "word": word,
                        "confidence": 40.0,
                        "reason": "unknown_word_no_near_matches",
                    })
        except ImportError:
            pass

    return {
        "page": round(page_conf, 1),
        "character": round(char_conf, 1),
        "word": round(word_conf, 1),
        "line": round(line_conf, 1),
        "paragraph": round(para_conf, 1),
        "uncertain_regions": uncertain_regions[:20],  # Top 20 uncertain words
    }

