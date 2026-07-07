"""
PDF text extraction via image-first OCR.

Every page is rasterised to a high-resolution image so legacy Nepali fonts
(Preeti, Kantipur, etc.) are read visually instead of through biased text layers.
"""
from __future__ import annotations

from typing import Any

from app.extract.ocr_pipeline import ocr_image
from app.extract.render import render_document_pages


def extract_pdf(pdf_bytes: bytes, lang: str = "auto") -> dict[str, Any]:
    """
    Extract text from PDF using image-first OCR on every page.
    """
    page_images = render_document_pages(pdf_bytes, filetype="pdf")

    page_texts: list[str] = []
    confidences: list[float] = []
    methods: list[str] = []

    for image_bgr in page_images:
        result = ocr_image(image_bgr, lang, digital=True)
        page_texts.append(result["text"])
        confidences.append(result["mean_confidence"])
        methods.append("image_ocr")

    final_text = "\n\n--- Page Break ---\n\n".join(page_texts)
    mean_confidence = (
        round(sum(confidences) / len(confidences), 2) if confidences else 0.0
    )

    return {
        "text": final_text,
        "pages": len(page_images),
        "method_per_page": methods,
        "method": "image_ocr",
        "mean_confidence": mean_confidence,
        "had_legacy_fonts": False,
        "detected_fonts": [],
    }
