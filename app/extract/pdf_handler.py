"""
PDF text extraction — uses the Document Intelligence System.
"""
from __future__ import annotations
from typing import Any

LARGE_PAGE_THRESHOLD = 100


def extract_pdf(pdf_bytes: bytes, lang: str = "auto", mode: str = "auto") -> dict[str, Any]:
    """
    Extract text from a PDF using the Document Intelligence System.

    Pipeline:
    1. Document Intelligence analysis (BEFORE any extraction)
    2. Per-page extraction with adaptive routing
    3. Cross-page validation and correction
    4. Semantic validation
    5. Error memory update
    """
    import fitz
    from app.logging_config import get_logger
    logger = get_logger("PDFHandler")

    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        page_count = len(doc)
        doc.close()
    except Exception as exc:
        raise ValueError(f"Cannot open PDF: {exc}") from exc

    if mode == "ocr":
        # Force OCR pipeline for scanned documents
        from app.extract.document_ocr import ocr_document, format_page_results
        results, meta = ocr_document(pdf_bytes, "pdf", lang, mode="ocr")
        return format_page_results(results, meta)

    # === PHASE 1: Document Intelligence (BEFORE extraction) ===
    try:
        from app.intelligence.document_intelligence import analyze_document
        doc_intel = analyze_document(pdf_bytes)
        logger.info(
            "Document Intelligence: family=%s, domain=%s, pipeline=%s, "
            "pages=%d, scanned=%s, legacy=%s",
            doc_intel.family.value, doc_intel.domain,
            doc_intel.recommended_pipeline,
            doc_intel.total_pages, doc_intel.is_scanned, doc_intel.is_legacy,
        )
    except Exception as e:
        logger.warning("Document Intelligence failed, using fallback: %s", e)
        doc_intel = None

    # === PHASE 2: Extraction with Intelligence-Driven Routing ===
    try:
        if doc_intel and doc_intel.recommended_pipeline == "direct_unicode":
            # Pure Unicode — fastest path
            result = _extract_with_intelligence(pdf_bytes, doc_intel, lang)
        elif doc_intel and doc_intel.recommended_pipeline == "direct_legacy_conversion":
            # Legacy fonts — direct extraction with conversion
            result = _extract_with_intelligence(pdf_bytes, doc_intel, lang)
        elif doc_intel and doc_intel.recommended_pipeline == "ocr_full":
            # Fully scanned — OCR pipeline
            from app.extract.document_ocr import ocr_document, format_page_results
            results, meta = ocr_document(pdf_bytes, "pdf", lang, mode="ocr")
            result = format_page_results(results, meta)
        else:
            # Hybrid or unknown — use reconstruction engine
            result = _extract_with_intelligence(pdf_bytes, doc_intel, lang)

        # === PHASE 3: Post-extraction Intelligence ===
        if doc_intel:
            result = _apply_post_extraction_intelligence(result, doc_intel, pdf_bytes)

        return result

    except Exception as exc:
        logger.warning("Intelligence pipeline failed, falling back: %s", exc)
        # Fallback to reconstruction engine
        try:
            from app.extract.reconstruction_engine import reconstruct_document
            result = reconstruct_document(pdf_bytes, lang=lang)
            return {
                "text": result["text"],
                "pages": result["pages"],
                "method": result["method"],
                "mean_confidence": result.get("mean_confidence", 0),
                "confidence": result.get("confidence", {}),
                "validation": result.get("validation", {}),
                "font_analysis": result.get("font_analysis", {}),
            }
        except Exception:
            pass

        # Final fallback
        if page_count > LARGE_PAGE_THRESHOLD:
            from app.extract.streaming_extractor import extract_large_pdf_streaming
            result = extract_large_pdf_streaming(pdf_bytes, lang=lang)
            return {
                "text": result["text"],
                "pages": result["pages"],
                "method": result["method"],
                "mean_confidence": 90.0,
            }

        from app.extract.document_ocr import ocr_document, format_page_results
        results, meta = ocr_document(pdf_bytes, "pdf", lang, mode="auto")
        return format_page_results(results, meta)


def _extract_with_intelligence(
    pdf_bytes: bytes,
    doc_intel: Any,
    lang: str,
) -> dict[str, Any]:
    """
    Extract text using the Document Intelligence System.

    Uses per-page, per-region adaptive routing.
    """
    import fitz
    import gc
    from app.intelligence.ocr_router import extract_page
    from app.intelligence.cross_page import PageText, analyze_cross_page, apply_cross_page_corrections
    from app.extract.direct_extract import build_font_lookup
    from app.nlp.font_detector import analyse_document_fonts
    from app.logging_config import get_logger

    logger = get_logger("PDFHandler")

    # Build font lookup
    font_analysis = analyse_document_fonts(pdf_bytes)
    font_lookup = build_font_lookup(font_analysis)

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page_texts: list[str] = []
    page_metas: list[dict] = []
    cross_page_data: list[PageText] = []

    try:
        resolved_lang = lang if lang != "auto" else doc_intel.language_hint if doc_intel else "nep+eng"

        for idx in range(len(doc)):
            page = doc.load_page(idx)

            # Get page intelligence
            page_intel = None
            if doc_intel and idx < len(doc_intel.page_intelligence):
                page_intel = doc_intel.page_intelligence[idx]

            if page_intel:
                # Use intelligent routing
                result = extract_page(page, page_intel, font_lookup, resolved_lang)
            else:
                # Fallback to direct extraction
                from app.extract.direct_extract import extract_page_direct
                direct = extract_page_direct(page, font_lookup)
                result = {
                    "text": direct.get("text", ""),
                    "confidence": direct.get("confidence", 0),
                    "method": direct.get("method", "unknown"),
                    "regions": [],
                }

            page_texts.append(result["text"])
            page_metas.append(result)

            # Build cross-page data
            text = result["text"]
            lines = text.split("\n")
            cross_page_data.append(PageText(
                page_number=idx + 1,
                text=text,
                first_line=lines[0] if lines else "",
                last_line=lines[-1] if lines else "",
                ends_mid_sentence=bool(
                    text.strip() and
                    not text.strip().endswith(("।", ".", "?", "!"))
                ),
            ))

            if idx % 20 == 0 or idx == len(doc) - 1:
                logger.info(
                    "Page %d/%d: method=%s, confidence=%.1f",
                    idx + 1, len(doc),
                    result.get("method", "unknown"),
                    result.get("confidence", 0),
                )
            gc.collect()

    finally:
        doc.close()

    # Apply cross-page corrections
    if len(page_texts) > 2:
        cross_result = analyze_cross_page(cross_page_data)
        page_texts = apply_cross_page_corrections(page_texts, cross_result)

    # Combine pages
    if len(page_texts) == 1:
        final_text = page_texts[0]
    else:
        final_text = "\n\n--- Page Break ---\n\n".join(page_texts)

    confidences = [m.get("confidence", 0) for m in page_metas]
    mean_conf = sum(confidences) / len(confidences) if confidences else 0

    return {
        "text": final_text,
        "pages": len(page_texts),
        "method": "document_intelligence",
        "mean_confidence": round(mean_conf, 1),
        "font_analysis": font_analysis,
        "document_family": doc_intel.family.value if doc_intel else "unknown",
        "domain": doc_intel.domain if doc_intel else "general",
        "pipeline": doc_intel.recommended_pipeline if doc_intel else "fallback",
    }


def _apply_post_extraction_intelligence(
    result: dict[str, Any],
    doc_intel: Any,
    pdf_bytes: bytes,
) -> dict[str, Any]:
    """Apply post-extraction intelligence: semantic validation, knowledge base correction."""
    from app.logging_config import get_logger
    logger = get_logger("PDFHandler")

    text = result.get("text", "")
    if not text.strip():
        return result

    # 1. Knowledge base word correction
    try:
        from app.intelligence.nepal_knowledge_base import correct_word
        import re

        words = re.findall(r"[\u0900-\u097F]+", text)
        corrections_applied = 0

        for word in words:
            corrected, conf, source = correct_word(word, doc_intel.domain)
            if corrected != word and conf >= 75:
                text = text.replace(word, corrected, 1)
                corrections_applied += 1

        if corrections_applied > 0:
            result["text"] = text
            result["knowledge_base_corrections"] = corrections_applied
            logger.info("Applied %d knowledge base corrections", corrections_applied)
    except Exception as e:
        logger.debug("Knowledge base correction skipped: %s", e)

    # 2. Semantic validation
    try:
        from app.intelligence.semantic_validator import validate_document_semantics
        semantic_result = validate_document_semantics(
            text, domain=doc_intel.domain
        )
        result["semantic_validation"] = {
            "is_valid": semantic_result.is_valid,
            "issues": len(semantic_result.issues),
            "confidence_adjustment": semantic_result.confidence_adjustment,
        }
        if semantic_result.confidence_adjustment != 0:
            result["mean_confidence"] = max(
                0,
                result.get("mean_confidence", 0) + semantic_result.confidence_adjustment,
            )
    except Exception as e:
        logger.debug("Semantic validation skipped: %s", e)

    # 3. Record in error memory (for learning)
    try:
        from app.intelligence.error_memory import get_error_memory
        db = get_error_memory()
        # Save any corrections we made
        if result.get("knowledge_base_corrections", 0) > 0:
            db.save()
    except Exception:
        pass

    return result
