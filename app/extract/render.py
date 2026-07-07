"""
Render document pages to images for image-first OCR extraction.
"""
from __future__ import annotations

import fitz
import numpy as np

from app.config import PDF_RENDER_DPI


def pixmap_to_bgr(pix: fitz.Pixmap) -> np.ndarray:
    """Convert a PyMuPDF Pixmap to a BGR numpy array."""
    if pix.n >= 3:
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)
        if pix.n == 4:
            bgr = img[:, :, [2, 1, 0]]
            alpha = img[:, :, 3].astype(np.float64) / 255.0
            bg = np.ones_like(bgr, dtype=np.float64) * 255.0
            result = bgr * alpha[..., None] + bg * (1.0 - alpha[..., None])
            return np.clip(result, 0, 255).astype(np.uint8)
        return img[:, :, [2, 1, 0]].copy()

    gray = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w)
    return np.stack((gray,) * 3, axis=-1)


def render_document_pages(
    document_bytes: bytes,
    filetype: str,
    dpi: int = PDF_RENDER_DPI,
) -> list[np.ndarray]:
    """
    Open a PDF or Office document and render every page to a BGR image.

    PyMuPDF rasterises embedded legacy fonts (Preeti, Kantipur, etc.) so OCR
    reads visible Devanagari glyphs instead of font-encoded ASCII text layers.
    """
    try:
        doc = fitz.open(stream=document_bytes, filetype=filetype)
    except Exception as exc:
        raise ValueError(f"Failed to open {filetype.upper()} document: {exc}") from exc

    pages: list[np.ndarray] = []
    try:
        for page_index in range(len(doc)):
            page = doc.load_page(page_index)
            pix = page.get_pixmap(dpi=dpi, alpha=False)
            pages.append(pixmap_to_bgr(pix))
    finally:
        doc.close()

    if not pages:
        raise ValueError(f"No pages found in {filetype.upper()} document.")

    return pages
