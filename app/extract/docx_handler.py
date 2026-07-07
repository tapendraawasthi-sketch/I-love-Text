"""
DOCX text extraction via image-first OCR.

Office documents are rasterised page-by-page so embedded legacy Nepali fonts
are OCR'd from visible glyphs instead of font-encoded text layers.
"""
from __future__ import annotations

from typing import Any

from app.extract.ocr_pipeline import ocr_image
from app.extract.render import render_document_pages


def extract_docx(docx_bytes: bytes, lang: str = "auto") -> dict[str, Any]:
    """Extract text from DOCX by rendering each page to an image and running OCR."""
    page_images = render_document_pages(docx_bytes, filetype="docx")

    page_texts: list[str] = []
    confidences: list[float] = []

    for image_bgr in page_images:
        result = ocr_image(image_bgr, lang, digital=True)
        page_texts.append(result["text"])
        confidences.append(result["mean_confidence"])

    if len(page_texts) == 1:
        final_text = page_texts[0]
    else:
        final_text = "\n\n--- Page Break ---\n\n".join(page_texts)

    mean_confidence = (
        round(sum(confidences) / len(confidences), 2) if confidences else 0.0
    )

    return {
        "text": final_text,
        "pages": len(page_images),
        "method_per_page": ["image_ocr"] * len(page_images),
        "method": "image_ocr",
        "mean_confidence": mean_confidence,
        "had_legacy_fonts": False,
        "detected_fonts": [],
    }
