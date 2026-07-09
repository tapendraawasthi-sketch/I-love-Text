"""
Document Reconstruction Engine - the master pipeline.

This replaces the old "text extraction with improvements" approach
with a proper "document reconstruction engine."

Pipeline:
    PDF bytes
    → Font Analysis
    → Per-page extraction (with per-span classification)
    → Document Object Model construction
    → Column detection & reading order
    → Element classification (headings, lists, tables, footnotes)
    → Header/footer detection (probabilistic)
    → Multi-engine validation (for low-confidence pages)
    → Confidence scoring
    → Final validation
    → Text serialization
"""
from __future__ import annotations

import gc
from typing import Any

import fitz

from app.extract.document_model import (
    DocumentModel, PageModel, DocumentElement, ElementType,
    TextLine, TextSpan, BBox, FontEncoding,
)
from app.extract.span_classifier import classify_span
from app.extract.layout_engine import (
    assign_reading_order, detect_page_regions,
)
from app.extract.element_classifier import (
    classify_all_elements, compute_body_font_size,
)
from app.extract.header_footer_detector import (
    classify_page_elements, detect_running_elements_smart,
)
from app.extract.table_engine import (
    extract_tables_from_page, get_table_bboxes,
)
from app.extract.confidence_scorer import score_document, score_page
from app.extract.extraction_validator import validate_extraction
from app.extract.multi_engine_validator import (
    validate_page_extraction, select_best_extraction,
)
from app.nlp.font_detector import analyse_document_fonts
from app.extract.direct_extract import build_font_lookup
from app.logging_config import get_logger

logger = get_logger("ReconstructionEngine")


def reconstruct_document(
    pdf_bytes: bytes,
    lang: str = "auto",
    *,
    validate: bool = True,
    max_pages: int | None = None,
) -> dict[str, Any]:
    """
    Main entry point: reconstruct a PDF document into structured text.
    
    Returns:
        {
            "text": str,           # Final human-readable text
            "model": DocumentModel, # Full document model
            "confidence": dict,    # Multi-dimensional confidence scores
            "validation": dict,    # Validation report
            "method": str,
            "pages": int,
        }
    """
    # Step 1: Font analysis
    logger.info("Step 1/7: Analyzing document fonts...")
    font_analysis = analyse_document_fonts(pdf_bytes)
    font_lookup = build_font_lookup(font_analysis)

    # Step 2: Open document and build page models
    logger.info("Step 2/7: Building document object model...")
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        raise ValueError(f"Cannot open PDF: {exc}") from exc

    total_pages = len(doc)
    if max_pages:
        total_pages = min(total_pages, max_pages)

    doc_model = DocumentModel(
        dominant_font_family=font_analysis.get("dominant_family", "unknown"),
        all_fonts=[f["raw_name"] for f in font_analysis.get("fonts_found", [])],
        legacy_fonts=[
            f["raw_name"] for f in font_analysis.get("fonts_found", [])
            if f.get("is_legacy")
        ],
    )

    try:
        for idx in range(total_pages):
            page = doc.load_page(idx)
            page_model = _extract_page_model(page, idx + 1, font_lookup)
            doc_model.add_page(page_model)

            if idx % 20 == 0 or idx == total_pages - 1:
                logger.info("  Page %d/%d processed", idx + 1, total_pages)

            gc.collect()
    finally:
        doc.close()

    # Step 3: Column detection and reading order
    logger.info("Step 3/7: Detecting columns and reading order...")
    for page_model in doc_model.pages:
        assign_reading_order(page_model)

    # Step 4: Element classification
    logger.info("Step 4/7: Classifying document elements...")
    for page_model in doc_model.pages:
        classify_all_elements(page_model.elements, page_model.width)

    # Step 5: Header/footer detection
    logger.info("Step 5/7: Detecting headers and footers...")
    body_sizes = [
        compute_body_font_size(p.elements) for p in doc_model.pages
    ]
    avg_body_size = (
        sum(body_sizes) / len(body_sizes) if body_sizes else 10.0
    )

    for page_model in doc_model.pages:
        headers, body, footers, footnotes = detect_page_regions(
            page_model.elements, page_model.height
        )
        page_model.header_elements = headers
        page_model.elements = body
        page_model.footer_elements = footers
        page_model.footnote_elements = footnotes

    detect_running_elements_smart(doc_model)

    # Step 6: Confidence scoring
    logger.info("Step 6/7: Computing confidence scores...")
    confidence = score_document(doc_model)

    # Step 7: Validation
    validation = {}
    if validate:
        logger.info("Step 7/7: Validating extraction...")
        validation = validate_extraction(doc_model, pdf_bytes)
    else:
        logger.info("Step 7/7: Validation skipped")

    # Serialize to text
    final_text = doc_model.serialize_to_text(
        include_page_separators=total_pages > 1,
        suppress_running=True,
    )

    doc_model.method = "document_reconstruction"

    return {
        "text": final_text,
        "model": doc_model,
        "confidence": confidence,
        "validation": validation,
        "method": "document_reconstruction",
        "pages": total_pages,
        "font_analysis": font_analysis,
        "quality": confidence.get("overall", 0),
        "mean_confidence": confidence.get("overall", 0),
    }


def _extract_page_model(
    page: fitz.Page,
    page_number: int,
    font_lookup: dict[str, Any],
) -> PageModel:
    """
    Build a PageModel from a fitz.Page.
    
    Extracts every span independently, classifies fonts per-span,
    and builds the element tree.
    """
    page_rect = page.rect
    page_model = PageModel(
        page_number=page_number,
        width=page_rect.width,
        height=page_rect.height,
    )

    # Get table regions to exclude from text extraction
    table_bboxes = get_table_bboxes(page)

    # Extract tables as DocumentElements
    tables = extract_tables_from_page(page, font_lookup)
    page_model.elements.extend(tables)

    # Extract text blocks
    page_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)

    for block in page_dict.get("blocks", []):
        if block.get("type") != 0:
            continue

        block_bbox = BBox.from_tuple(block.get("bbox", (0, 0, 0, 0)))

        # Skip blocks inside table regions
        if _in_any_table(block_bbox, table_bboxes):
            continue

        block_lines: list[TextLine] = []
        block_font_sizes: list[float] = []
        block_is_bold = False
        detected_fonts: list[str] = []
        legacy_fonts: list[str] = []

        for line_data in block.get("lines", []):
            line = TextLine()

            for span_data in line_data.get("spans", []):
                font_name = span_data.get("font", "")
                raw_text = span_data.get("text", "")
                span_bbox = span_data.get("bbox", (0, 0, 0, 0))
                font_size = span_data.get("size", 0.0)
                flags = span_data.get("flags", 0)
                color = span_data.get("color", 0)

                if not raw_text:
                    continue

                is_bold = bool(flags & (1 << 4))
                is_italic = bool(flags & (1 << 1))

                # Classify and convert each span independently
                classified_span = classify_span(
                    raw_text=raw_text,
                    font_name=font_name,
                    font_size=font_size,
                    is_bold=is_bold,
                    is_italic=is_italic,
                    color=color,
                    bbox=span_bbox,
                    font_lookup=font_lookup,
                )

                line.spans.append(classified_span)

                detected_fonts.append(font_name)
                if classified_span.font_encoding not in (
                    FontEncoding.UNICODE, FontEncoding.UNKNOWN
                ):
                    legacy_fonts.append(font_name)

                block_font_sizes.append(font_size)
                if is_bold:
                    block_is_bold = True

            if line.spans:
                line.compute_bbox()
                block_lines.append(line)

        if not block_lines:
            continue

        # Compute block-level properties
        avg_font_size = (
            sum(block_font_sizes) / len(block_font_sizes)
            if block_font_sizes else 0.0
        )

        elem = DocumentElement(
            element_type=ElementType.PARAGRAPH,  # Will be reclassified later
            bbox=block_bbox,
            lines=block_lines,
            font_size=avg_font_size,
            is_bold=block_is_bold,
        )

        page_model.elements.append(elem)
        page_model.fonts_detected.extend(detected_fonts)
        page_model.legacy_fonts_detected.extend(legacy_fonts)

    return page_model


def _in_any_table(
    bbox: BBox,
    table_bboxes: list[tuple[float, float, float, float]],
) -> bool:
    """Check if a bbox overlaps any table region."""
    for tb in table_bboxes:
        t = BBox.from_tuple(tb)
        if bbox.overlaps(t, threshold=0.3):
            return True
    return False
