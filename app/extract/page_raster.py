"""
Optimized page rasterisation: grayscale render + text-safe JPEG compression.

Used only when OCR is required (scanned pages). Digital text pages skip this entirely.
"""
from __future__ import annotations

import gc
from typing import Any

import fitz
import numpy as np

from app.extract.render import pixmap_to_bgr


def _pixmap_to_jpeg(pix: fitz.Pixmap, quality: int) -> bytes:
    gray = pix if pix.n == 1 else fitz.Pixmap(fitz.csGRAY, pix)
    try:
        return gray.tobytes("jpeg", jpg_quality=quality)
    except TypeError:
        return gray.tobytes(output="jpeg", jpg_quality=quality)


def adaptive_jpeg_quality(
    pix: fitz.Pixmap,
    *,
    start_quality: int = 88,
    min_quality: int = 82,
    max_bytes: int = 900_000,
) -> tuple[bytes, int]:
    """
    Pick the highest JPEG quality that keeps the page under max_bytes.
    Never goes below min_quality — text stays readable for OCR.
    """
    quality = start_quality
    jpeg = _pixmap_to_jpeg(pix, quality)
    while len(jpeg) > max_bytes and quality > min_quality:
        quality -= 2
        jpeg = _pixmap_to_jpeg(pix, quality)
    return jpeg, quality


def rasterize_page_for_ocr(
    page: fitz.Page,
    dpi: int,
    *,
    jpeg_quality: int | None = None,
    max_jpeg_bytes: int = 900_000,
) -> tuple[np.ndarray, dict[str, Any]]:
    """
    Rasterise one page to BGR for OCR, with optional text-safe JPEG compression metadata.
    """
    pix = page.get_pixmap(dpi=dpi, alpha=False, colorspace=fitz.csGRAY)
    try:
        if jpeg_quality is not None:
            jpeg = _pixmap_to_jpeg(pix, jpeg_quality)
            used_q = jpeg_quality
        else:
            jpeg, used_q = adaptive_jpeg_quality(pix, max_bytes=max_jpeg_bytes)

        image_bgr = pixmap_to_bgr(pix)
        meta = {
            "dpi": dpi,
            "width": pix.width,
            "height": pix.height,
            "jpeg_bytes": len(jpeg),
            "jpeg_quality": used_q,
        }
        return image_bgr, meta
    finally:
        del pix
        gc.collect()


def build_image_only_pdf_page(
    clean_doc: fitz.Document,
    pix: fitz.Pixmap,
    dpi: int,
    jpeg: bytes,
) -> None:
    """Embed a compressed JPEG as a single page in an image-only PDF."""
    width_pt = pix.width * 72.0 / dpi
    height_pt = pix.height * 72.0 / dpi
    page = clean_doc.new_page(width=width_pt, height=height_pt)
    page.insert_image(page.rect, stream=jpeg)
