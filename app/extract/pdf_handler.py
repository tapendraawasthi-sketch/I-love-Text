"""
PDF text extraction — auto-selects streaming for large documents.
"""
from __future__ import annotations
from typing import Any

LARGE_PAGE_THRESHOLD = 100   # use streaming extractor above this page count


def extract_pdf(pdf_bytes: bytes, lang: str = "auto", mode: str = "auto") -> dict[str, Any]:
    """
    Extract text from a PDF.

    Automatically uses the streaming batch extractor for PDFs with more than
    LARGE_PAGE_THRESHOLD pages to keep RAM usage bounded.
    """
    import fitz
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        page_count = len(doc)
        doc.close()
    except Exception as exc:
        raise ValueError(f"Cannot open PDF: {exc}") from exc

    if mode == "ocr":
        # Always use OCR pipeline for scanned documents
        from app.extract.document_ocr import ocr_document, format_page_results
        results, meta = ocr_document(pdf_bytes, "pdf", lang, mode="ocr")
        return format_page_results(results, meta)

    if page_count > LARGE_PAGE_THRESHOLD or mode == "direct":
        # Use streaming extractor for large PDFs or when direct mode is forced
        from app.extract.streaming_extractor import extract_large_pdf_streaming
        result = extract_large_pdf_streaming(pdf_bytes, lang=lang)
        return {
            "text": result["text"],
            "pages": result["pages"],
            "method": result["method"],
            "mean_confidence": 90.0,   # direct extraction is high confidence
            "legacy_fonts": result.get("legacy_fonts", []),
            "quality_report": result.get("quality_report", []),
        }

    # Small PDF: use existing auto-mode pipeline
    from app.extract.document_ocr import ocr_document, format_page_results
    results, meta = ocr_document(pdf_bytes, "pdf", lang, mode="auto")
    return format_page_results(results, meta)
