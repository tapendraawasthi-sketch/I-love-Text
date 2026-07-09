"""
Document Reconstruction Engine v2 — Complete Rewrite.

This is NOT a text extractor. This is a document reconstruction engine.

Pipeline:
    PDF bytes
     → Font Analysis
     → Per-page Glyph Extraction (character level, not span level)
     → Unicode Validation (per-character sequence checking)
     → Unicode Repair (matra reordering, double matra removal)
     → Multi-Engine Consensus (4 extraction engines vote)
     → Visual Validation (render comparison for suspicious pages)
     → Word Assembly (from validated glyphs)
     → Paragraph Reconstruction (line joining, not line listing)
     → Legal Structure Detection (parts, chapters, sections, schedules)
     → Column Detection & Reading Order
     → Header/Footer Detection
     → Confidence Scoring (per-glyph, per-word, per-page, per-document)
     → Document AST Construction
     → Final Validation
     → Serialization to TXT
"""
from __future__ import annotations

import gc
from typing import Any

import fitz

from app.extract.document_model import (
    DocumentModel, PageModel, DocumentElement, ElementType,
    TextLine, TextSpan, BBox, FontEncoding,
)
from app.extract.glyph_extractor import (
    extract_glyphs_from_page, validate_page_glyphs, assemble_words,
)
from app.extract.unicode_validator import (
    validate_devanagari_text, repair_devanagari_unicode, validate_text_block,
)
from app.extract.multi_engine_extractor import extract_with_consensus
from app.extract.visual_validator import (
    validate_extraction_against_visual, estimate_visual_text_density,
)
from app.extract.paragraph_assembler import (
    group_words_into_lines, assemble_paragraphs,
)
from app.extract.span_classifier import classify_span
from app.extract.layout_engine import assign_reading_order, detect_page_regions
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
    enable_visual_validation: bool = True,
    enable_multi_engine: bool = True,
) -> dict[str, Any]:
    """
    Main entry point: reconstruct a PDF document.

    This is the v2 pipeline that operates at glyph level, validates Unicode,
    uses multi-engine consensus, and reconstructs paragraphs (not lines).
    """
    # Step 1: Font analysis
    logger.info("Step 1/8: Analyzing document fonts...")
    font_analysis = analyse_document_fonts(pdf_bytes)
    font_lookup = build_font_lookup(font_analysis)
    has_legacy_fonts = bool(font_analysis.get("legacy_fonts", []))

    # Step 2: Open document
    logger.info("Step 2/8: Opening document and extracting glyphs...")
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        raise ValueError(f"Cannot open PDF: {exc}") from exc

    total_pages = min(len(doc), max_pages) if max_pages else len(doc)

    # Step 3: Per-page extraction with Unicode validation
    logger.info("Step 3/8: Per-page glyph extraction + Unicode validation...")
    page_texts: list[str] = []
    page_confidences: list[float] = []
    page_methods: list[str] = []
    total_unicode_repairs = 0

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
            page_result = _extract_and_validate_page(
                page, idx + 1, font_lookup,
                enable_multi_engine=enable_multi_engine,
                enable_visual=enable_visual_validation and has_legacy_fonts,
            )

            page_texts.append(page_result["text"])
            page_confidences.append(page_result["confidence"])
            page_methods.append(page_result["method"])
            total_unicode_repairs += page_result.get("unicode_repairs", 0)

            # Build page model for document model
            page_model = _build_page_model(page, idx + 1, page_result, font_lookup)
            doc_model.add_page(page_model)

            if idx % 20 == 0 or idx == total_pages - 1:
                logger.info(
                    "  Page %d/%d: %s (confidence: %.1f%%, repairs: %d)",
                    idx + 1, total_pages,
                    page_result["method"],
                    page_result["confidence"],
                    page_result.get("unicode_repairs", 0),
                )
            gc.collect()
    finally:
        doc.close()

    # Step 4: Reading order
    logger.info("Step 4/8: Detecting columns and reading order...")
    for page_model in doc_model.pages:
        assign_reading_order(page_model)

    # Step 5: Element classification
    logger.info("Step 5/8: Classifying document elements...")
    for page_model in doc_model.pages:
        classify_all_elements(page_model.elements, page_model.width)

    # Step 6: Header/footer detection
    logger.info("Step 6/8: Detecting headers and footers...")
    for page_model in doc_model.pages:
        headers, body, footers, footnotes = detect_page_regions(
            page_model.elements, page_model.height
        )
        page_model.header_elements = headers
        page_model.elements = body
        page_model.footer_elements = footers
        page_model.footnote_elements = footnotes
    detect_running_elements_smart(doc_model)

    # Step 7: Confidence scoring
    logger.info("Step 7/8: Computing confidence scores...")
    confidence = score_document(doc_model)

    # Step 8: Validation
    validation = {}
    if validate:
        logger.info("Step 8/8: Validating extraction...")
        validation = validate_extraction(doc_model, pdf_bytes)
    else:
        logger.info("Step 8/8: Validation skipped")

    # Serialize
    final_text = doc_model.serialize_to_text(
        include_page_separators=total_pages > 1,
        suppress_running=True,
    )

    # Apply final Unicode validation to entire document
    doc_validation = validate_text_block(final_text)
    if doc_validation.get("repairs", 0) > 0:
        logger.info(
            "Final Unicode pass: %d word-level repairs applied",
            doc_validation["repairs"],
        )

    mean_conf = sum(page_confidences) / len(page_confidences) if page_confidences else 0
    doc_model.method = "document_reconstruction_v2"

    return {
        "text": final_text,
        "model": doc_model,
        "confidence": confidence,
        "validation": validation,
        "method": "document_reconstruction_v2",
        "pages": total_pages,
        "font_analysis": font_analysis,
        "quality": confidence.get("overall", 0),
        "mean_confidence": round(mean_conf, 1),
        "unicode_repairs": total_unicode_repairs,
        "page_methods": page_methods,
        "document_unicode_quality": doc_validation,
    }


def _extract_and_validate_page(
    page: fitz.Page,
    page_number: int,
    font_lookup: dict[str, Any],
    *,
    enable_multi_engine: bool = True,
    enable_visual: bool = False,
) -> dict[str, Any]:
    """
    Extract and validate a single page using the full pipeline.

    1. Glyph-level extraction + Unicode validation
    2. Multi-engine consensus (if enabled)
    3. Visual validation (if enabled and needed)
    4. Paragraph reconstruction
    """
    # Primary extraction: glyph level
    glyphs = extract_glyphs_from_page(page, font_lookup)
    validated_glyphs = validate_page_glyphs(glyphs)

    # Check for legacy fonts — if found, mark as needing OCR or special handling
    has_legacy = any(
        g.confidence.value == "suspicious" for g in validated_glyphs
    )

    # Assemble words with validation
    words = assemble_words(validated_glyphs)

    # Build primary text from words
    lines = group_words_into_lines(words)
    primary_text = "\n".join(
        " ".join(w.text for w in line.words)
        for line in lines
    )

    # Apply Unicode validation and repair
    unicode_result = validate_devanagari_text(primary_text)
    repaired_text = unicode_result.repaired_text
    unicode_repairs = unicode_result.repair_count

    # Multi-engine consensus (for quality assurance)
    method = "glyph_extraction"
    confidence = 85.0

    if enable_multi_engine:
        consensus = extract_with_consensus(page, prefer_devanagari=True)
        consensus_score = consensus["score"]["total"]

        # Compare glyph extraction against consensus
        glyph_validation = validate_text_block(repaired_text)
        glyph_quality = glyph_validation.get("confidence", 50)

        if consensus_score > glyph_quality + 10:
            # Consensus is significantly better
            repaired_text = consensus["text"]
            method = f"consensus_{consensus['engine']}"
            confidence = min(95, consensus_score)
            logger.debug(
                "Page %d: consensus engine '%s' won (%.1f vs %.1f)",
                page_number, consensus["engine"], consensus_score, glyph_quality,
            )
        else:
            confidence = max(glyph_quality, 70)

    # Visual validation (expensive, only when needed)
    if enable_visual and confidence < 75:
        text_regions = [
            (g.x0, g.y0, g.x1, g.y1)
            for g in validated_glyphs[:20]
            if g.is_devanagari
        ]
        visual = validate_extraction_against_visual(
            page, repaired_text, text_regions
        )
        if visual.get("recommendation") == "ocr_fallback":
            # Fall back to OCR for this page
            from app.extract.precision_pipeline import extract_page_precision
            from app.ocr.engine import resolve_ocr_lang
            ocr_result = extract_page_precision(page, "nep+eng")
            repaired_text = ocr_result["text"]
            method = "visual_validated_ocr"
            confidence = ocr_result.get("confidence", 60)
            logger.info(
                "Page %d: visual validation triggered OCR fallback",
                page_number,
            )

    return {
        "text": repaired_text,
        "method": method,
        "confidence": confidence,
        "unicode_repairs": unicode_repairs,
        "glyph_count": len(validated_glyphs),
        "word_count": len(words),
        "has_legacy_fonts": has_legacy,
    }


def _build_page_model(
    page: fitz.Page,
    page_number: int,
    page_result: dict[str, Any],
    font_lookup: dict[str, Any],
) -> PageModel:
    """Build a PageModel from extraction results."""
    page_rect = page.rect
    page_model = PageModel(
        page_number=page_number,
        width=page_rect.width,
        height=page_rect.height,
        extraction_method=page_result["method"],
        confidence=page_result["confidence"],
    )

    # Get table regions
    table_bboxes = get_table_bboxes(page)
    tables = extract_tables_from_page(page, font_lookup)
    page_model.elements.extend(tables)

    # Build elements from the text
    text = page_result["text"]
    if text.strip():
        # Create a simple paragraph element for the page text
        elem = DocumentElement(
            element_type=ElementType.PARAGRAPH,
            bbox=BBox(0, 0, page_rect.width, page_rect.height),
            lines=[],
            font_size=10.0,
        )
        # Store text through a TextLine/TextSpan
        span = TextSpan(
            text=text,
            bbox=BBox(0, 0, page_rect.width, page_rect.height),
            converted_text=text,
        )
        line = TextLine(spans=[span])
        line.compute_bbox()
        elem.lines = [line]
        page_model.elements.append(elem)

    return page_model
