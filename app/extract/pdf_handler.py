"""
PDF text extraction — uses the Document Reconstruction Engine.
"""
from __future__ import annotations
from typing import Any

LARGE_PAGE_THRESHOLD = 100


def extract_pdf(pdf_bytes: bytes, lang: str = "auto", mode: str = "auto") -> dict[str, Any]:
    """
    Extract text from a PDF using the document reconstruction pipeline.
    """
    import fitz
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

    # Use the new reconstruction engine for all digital PDFs
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
    except Exception as exc:
        # Fallback to old pipeline if reconstruction fails
        from app.logging_config import get_logger
        logger = get_logger("PDFHandler")
        logger.warning("Reconstruction engine failed, falling back: %s", exc)

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
