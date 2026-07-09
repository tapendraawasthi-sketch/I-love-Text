"""
Document Reconstruction Engine v3 — fully integrated pipeline.

Pipeline:
    PDF → Font Program Parsing → Document Type Classification →
    Per-page Self-Repair Loop (font programs → direct → consensus → OCR) →
    Devanagari Grammar Validation → Paragraph Reconstruction →
    Probabilistic Document Graph → Page Similarity (headers/footers) →
    Semantic Table Parsing → Legal Structure Detection →
    Confidence Scoring → Benchmark-ready Serialization
"""
from __future__ import annotations

import gc
from typing import Any

import fitz

from app.extract.font_program_parser import extract_all_font_programs
from app.extract.document_type_classifier import classify_document, DocumentClass
from app.extract.self_repair_loop import self_repair_page
from app.extract.devanagari_grammar import repair_grammar_issues
from app.extract.probabilistic_model import (
    DocumentGraph, ProbabilisticElement, CandidateText, ElementRelation,
)
from app.extract.document_model import (
    DocumentModel, PageModel, DocumentElement, ElementType,
    TextLine, TextSpan, BBox, FontEncoding,
)
from app.extract.layout_engine import assign_reading_order, detect_page_regions
from app.extract.element_classifier import (
    classify_all_elements, compute_body_font_size,
)
from app.extract.header_footer_detector import detect_running_elements_smart
from app.extract.table_engine import extract_tables_from_page, get_table_bboxes
from app.extract.confidence_scorer import score_document
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
) -> dict[str, Any]:
    """
    Main entry point: reconstruct a PDF document into structured text.

    This is the v3 pipeline with:
    - Font program parsing (no more "legacy → OCR" default)
    - Self-repair loop (confidence-driven strategy selection)
    - Devanagari grammar validation
    - Probabilistic document graph
    - Document type classification
    """
    # Step 1: Font analysis + font program parsing
    logger.info("Step 1/9: Analyzing fonts and parsing font programs...")
    font_analysis = analyse_document_fonts(pdf_bytes)
    font_lookup = build_font_lookup(font_analysis)

    # Parse actual font programs for glyph-level reconstruction
    try:
        font_programs = extract_all_font_programs(pdf_bytes)
    except Exception as e:
        logger.warning("Font program parsing failed: %s", e)
        font_programs = {}

    # Step 2: Open document
    logger.info("Step 2/9: Opening document...")
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        raise ValueError(f"Cannot open PDF: {exc}") from exc

    total_pages = min(len(doc), max_pages) if max_pages else len(doc)

    # Step 3: Document type classification
    logger.info("Step 3/9: Classifying document type...")
    first_pages_text = []
    for i in range(min(5, total_pages)):
        page = doc.load_page(i)
        first_pages_text.append(page.get_text("text", sort=True))

    doc_type = classify_document(
        first_pages_text,
        font_analysis=font_analysis,
        page_count=total_pages,
    )
    logger.info(
        "Document type: %s (confidence: %.0f%%), evidence: %s",
        doc_type.primary_class.value,
        doc_type.confidence,
        "; ".join(doc_type.evidence[:3]),
    )

    # Step 4: Per-page extraction with self-repair loop
    logger.info("Step 4/9: Extracting pages with self-repair loop...")
    graph = DocumentGraph()
    graph.page_count = total_pages
    graph.metadata["document_type"] = doc_type.primary_class.value
    graph.metadata["font_analysis"] = font_analysis

    doc_model = DocumentModel(
        dominant_font_family=font_analysis.get("dominant_family", "unknown"),
        all_fonts=[f["raw_name"] for f in font_analysis.get("fonts_found", [])],
        legacy_fonts=[
            f["raw_name"] for f in font_analysis.get("fonts_found", [])
            if f.get("is_legacy")
        ],
    )

    page_methods: list[str] = []
    page_confidences: list[float] = []
    total_repairs = 0

    try:
        prev_element_id = None
        for idx in range(total_pages):
            page = doc.load_page(idx)

            # Self-repair loop: tries strategies in fidelity order
            attempt = self_repair_page(
                page, idx + 1, font_lookup,
                font_programs=font_programs,
                lang=lang if lang != "auto" else "nep+eng",
            )

            page_methods.append(attempt.strategy)
            page_confidences.append(attempt.composite_score)
            total_repairs += len(attempt.details.get("repairs", []))

            # Apply Devanagari grammar validation
            final_text, grammar_repairs = repair_grammar_issues(attempt.text)
            total_repairs += len(grammar_repairs)

            # Add to probabilistic graph
            element_id = f"page_{idx + 1}"
            elem = ProbabilisticElement(
                element_id=element_id,
                page_number=idx + 1,
            )
            elem.add_candidate(
                text=final_text,
                confidence=attempt.composite_score,
                source=attempt.strategy,
                unicode_valid=attempt.unicode_quality >= 70,
            )

            # If consensus had a different result, add as alternative
            if "consensus" in attempt.details:
                consensus_text = attempt.details.get("text", "")
                if consensus_text and consensus_text != final_text:
                    elem.add_candidate(
                        text=consensus_text,
                        confidence=attempt.confidence * 0.9,
                        source="consensus_alternative",
                    )

            graph.add_element(elem)

            # Add reading order edge
            if prev_element_id:
                graph.add_edge(
                    prev_element_id, element_id,
                    weight=90.0,
                    evidence=["sequential_page_order"],
                )
            prev_element_id = element_id

            # Build page model
            page_model = _build_page_model(page, idx + 1, final_text, font_lookup)
            doc_model.add_page(page_model)

            if idx % 20 == 0 or idx == total_pages - 1:
                logger.info(
                    "  Page %d/%d: strategy=%s score=%.1f repairs=%d",
                    idx + 1, total_pages,
                    attempt.strategy,
                    attempt.composite_score,
                    len(grammar_repairs),
                )
            gc.collect()

    finally:
        doc.close()

    # Step 5: Layout analysis
    logger.info("Step 5/9: Detecting columns and reading order...")
    for page_model in doc_model.pages:
        assign_reading_order(page_model)

    # Step 6: Element classification
    logger.info("Step 6/9: Classifying document elements...")
    for page_model in doc_model.pages:
        classify_all_elements(page_model.elements, page_model.width)

    # Step 7: Header/footer detection
    logger.info("Step 7/9: Detecting headers and footers...")
    for page_model in doc_model.pages:
        headers, body, footers, footnotes = detect_page_regions(
            page_model.elements, page_model.height
        )
        page_model.header_elements = headers
        page_model.elements = body
        page_model.footer_elements = footers
        page_model.footnote_elements = footnotes
    detect_running_elements_smart(doc_model)

    # Step 8: Confidence scoring
    logger.info("Step 8/9: Computing confidence scores...")
    confidence = score_document(doc_model)

    # Step 9: Validation
    validation = {}
    if validate:
        logger.info("Step 9/9: Validating extraction...")
        validation = validate_extraction(doc_model, pdf_bytes)

    # Serialize using the best available path
    # Try probabilistic graph first, fall back to doc model
    final_text = graph.serialize_best_path(
        include_page_separators=total_pages > 1,
    )

    if not final_text.strip():
        final_text = doc_model.serialize_to_text(
            include_page_separators=total_pages > 1,
            suppress_running=True,
        )

    mean_conf = sum(page_confidences) / len(page_confidences) if page_confidences else 0

    return {
        "text": final_text,
        "model": doc_model,
        "graph": graph,
        "confidence": confidence,
        "validation": validation,
        "method": "document_reconstruction_v3",
        "pages": total_pages,
        "font_analysis": font_analysis,
        "font_programs_parsed": len(font_programs),
        "document_type": doc_type.primary_class.value,
        "document_type_confidence": doc_type.confidence,
        "quality": confidence.get("overall", 0),
        "mean_confidence": round(mean_conf, 1),
        "total_repairs": total_repairs,
        "page_methods": page_methods,
        "graph_report": graph.confidence_report(),
    }


def _build_page_model(
    page: fitz.Page,
    page_number: int,
    text: str,
    font_lookup: dict[str, Any],
) -> PageModel:
    """Build a PageModel from extraction results."""
    page_rect = page.rect
    page_model = PageModel(
        page_number=page_number,
        width=page_rect.width,
        height=page_rect.height,
    )

    # Extract tables
    table_bboxes = get_table_bboxes(page)
    tables = extract_tables_from_page(page, font_lookup)
    page_model.elements.extend(tables)

    # Add text as element
    if text.strip():
        span = TextSpan(
            text=text,
            bbox=BBox(0, 0, page_rect.width, page_rect.height),
            converted_text=text,
        )
        line = TextLine(spans=[span])
        line.compute_bbox()
        elem = DocumentElement(
            element_type=ElementType.PARAGRAPH,
            bbox=BBox(0, 0, page_rect.width, page_rect.height),
            lines=[line],
        )
        page_model.elements.append(elem)

    return page_model
