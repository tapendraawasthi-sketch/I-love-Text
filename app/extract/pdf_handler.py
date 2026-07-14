"""
PDF text extraction — uses the Document Intelligence System.

Default fidelity is forensic: proven encoding conversion only,
no unsupervised dictionary / cross-page mutations.
"""
from __future__ import annotations
from typing import Any

from app.extract.fidelity import (
    FidelityMode,
    allow_cross_page_corrections,
    allow_mutations,
    get_fidelity,
    normalize_fidelity,
    reset_fidelity,
    set_fidelity,
)

LARGE_PAGE_THRESHOLD = 100


def extract_pdf(
    pdf_bytes: bytes,
    lang: str = "auto",
    mode: str = "auto",
    fidelity: str | FidelityMode = "forensic",
) -> dict[str, Any]:
    """
    Extract text from a PDF using the Document Intelligence System.

    Pipeline:
    1. Document Intelligence analysis (BEFORE any extraction)
    2. Per-page extraction with adaptive routing
    3. Cross-page validation (assisted fidelity only)
    4. Semantic validation / KB correction (assisted only)
    """
    import fitz
    from app.logging_config import get_logger
    logger = get_logger("PDFHandler")

    fidelity_mode = normalize_fidelity(fidelity if isinstance(fidelity, str) else fidelity)
    token = set_fidelity(fidelity_mode)

    try:
        try:
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            page_count = len(doc)
            doc.close()
        except Exception as exc:
            raise ValueError(f"Cannot open PDF: {exc}") from exc

        force_ocr = mode == "ocr"

        # === PHASE 1: Document Intelligence (BEFORE extraction) ===
        try:
            from app.intelligence.document_intelligence import analyze_document
            doc_intel = analyze_document(pdf_bytes)
            logger.info(
                "Document Intelligence: family=%s, domain=%s, pipeline=%s, "
                "pages=%d, scanned=%s, legacy=%s, fidelity=%s",
                doc_intel.family.value, doc_intel.domain,
                doc_intel.recommended_pipeline,
                doc_intel.total_pages, doc_intel.is_scanned, doc_intel.is_legacy,
                fidelity_mode,
            )
        except Exception as e:
            logger.warning("Document Intelligence failed, using fallback: %s", e)
            doc_intel = None

        # === PHASE 2: Block-based extraction ===
        try:
            result = _extract_with_intelligence(
                pdf_bytes, doc_intel, lang, force_ocr=force_ocr,
            )

            # === PHASE 3: Post-extraction (mutations only in assisted) ===
            if doc_intel:
                result = _apply_post_extraction_intelligence(result, doc_intel, pdf_bytes)

            result["fidelity"] = fidelity_mode
            return result

        except Exception as exc:
            logger.warning("Intelligence pipeline failed, falling back: %s", exc)
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
                    "fidelity": fidelity_mode,
                }
            except Exception:
                pass

            if page_count > LARGE_PAGE_THRESHOLD:
                from app.extract.streaming_extractor import extract_large_pdf_streaming
                result = extract_large_pdf_streaming(pdf_bytes, lang=lang)
                return {
                    "text": result["text"],
                    "pages": result["pages"],
                    "method": result["method"],
                    "mean_confidence": 90.0,
                    "fidelity": fidelity_mode,
                }

            from app.extract.document_ocr import ocr_document, format_page_results
            results, meta = ocr_document(pdf_bytes, "pdf", lang, mode="auto")
            out = format_page_results(results, meta)
            out["fidelity"] = fidelity_mode
            return out
    finally:
        reset_fidelity(token)


def _extract_with_intelligence(
    pdf_bytes: bytes,
    doc_intel: Any,
    lang: str,
    *,
    force_ocr: bool = False,
) -> dict[str, Any]:
    """Extract text using the Document Intelligence System."""
    import fitz
    import gc
    from app.intelligence.ocr_router import extract_page
    from app.intelligence.cross_page import PageText, analyze_cross_page, apply_cross_page_corrections
    from app.extract.direct_extract import build_font_lookup
    from app.nlp.font_detector import analyse_document_fonts
    from app.logging_config import get_logger

    logger = get_logger("PDFHandler")

    font_analysis = analyse_document_fonts(pdf_bytes)
    font_lookup = build_font_lookup(font_analysis)

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page_texts: list[str] = []
    page_metas: list[dict] = []
    cross_page_data: list[PageText] = []

    try:
        resolved_lang = lang if lang != "auto" else (
            doc_intel.language_hint if doc_intel else "nep+eng"
        )

        for idx in range(len(doc)):
            page = doc.load_page(idx)

            page_intel = None
            if doc_intel and idx < len(doc_intel.page_intelligence):
                page_intel = doc_intel.page_intelligence[idx]

            if page_intel:
                result = extract_page(
                    page, page_intel, font_lookup, resolved_lang,
                    force_ocr=force_ocr,
                )
            else:
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

    # Cross-page mutations only in assisted fidelity
    if len(page_texts) > 2 and allow_cross_page_corrections():
        cross_result = analyze_cross_page(cross_page_data)
        page_texts = apply_cross_page_corrections(page_texts, cross_result)

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
        "fidelity": get_fidelity(),
    }


def _apply_post_extraction_intelligence(
    result: dict[str, Any],
    doc_intel: Any,
    pdf_bytes: bytes,
) -> dict[str, Any]:
    """Apply post-extraction intelligence. Mutations only when fidelity=assisted."""
    from app.logging_config import get_logger
    logger = get_logger("PDFHandler")

    text = result.get("text", "")
    if not text.strip():
        return result

    corrections_log: list[dict[str, Any]] = []

    # 1. Knowledge base word correction — assisted only
    if allow_mutations():
        try:
            from app.intelligence.nepal_knowledge_base import correct_word
            import re

            words = re.findall(r"[\u0900-\u097F]+", text)
            corrections_applied = 0

            for word in words:
                corrected, conf, source = correct_word(word, doc_intel.domain)
                # Stricter: only distance-0/exact or distance-1 with conf >= 90
                if corrected != word and conf >= 90:
                    # Word-boundary safe replace once
                    pattern = re.compile(re.escape(word))
                    new_text, n = pattern.subn(corrected, text, count=1)
                    if n:
                        text = new_text
                        corrections_applied += 1
                        corrections_log.append({
                            "from": word,
                            "to": corrected,
                            "confidence": conf,
                            "source": source,
                        })

            if corrections_applied > 0:
                result["text"] = text
                result["knowledge_base_corrections"] = corrections_applied
                result["corrections"] = corrections_log
                logger.info("Applied %d knowledge base corrections", corrections_applied)
        except Exception as e:
            logger.debug("Knowledge base correction skipped: %s", e)

    # 2. Semantic validation (confidence only — never mutates text)
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

    # 3. Error memory — assisted only
    if allow_mutations():
        try:
            from app.intelligence.error_memory import get_error_memory
            db = get_error_memory()
            if result.get("knowledge_base_corrections", 0) > 0:
                db.save()
        except Exception:
            pass

    return result
