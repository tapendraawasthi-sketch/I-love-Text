"""
Image text extraction via OCR.
"""
from __future__ import annotations

from typing import Any

from app.extract.ocr_pipeline import ocr_image
from app.ocr.preprocess import load_image_bytes


def extract_image(image_bytes: bytes, lang: str = "auto") -> dict[str, Any]:
    """Extract text from images using preprocessing + layout-aware OCR."""
    try:
        image_bgr = load_image_bytes(image_bytes)
    except Exception as exc:
        raise ValueError(f"Failed to load image: {exc}") from exc

    result = ocr_image(image_bgr, lang, digital=False)

    return {
        "text": result["text"],
        "mean_confidence": result["mean_confidence"],
        "method": "image_ocr",
        "lang_used": result.get("lang_used", lang),
    }
