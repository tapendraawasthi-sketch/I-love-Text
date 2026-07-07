"""
Bias-free document pipeline: PDF/DOCX → images → image-only PDF → OCR.

Legacy font text layers (Preeti, Kantipur, etc.) are destroyed when each page is
rasterised and rebuilt as a JPEG image inside a new PDF. OCR then reads only
what is visibly printed on the page.
"""
from __future__ import annotations

import gc
from typing import Any

import fitz

from app.config import (
    SANITIZE_DPI,
    SANITIZE_DPI_LARGE,
    SANITIZE_DPI_MEDIUM,
    SANITIZE_JPEG_QUALITY,
    is_fast_ocr_mode,
)
from app.extract.ocr_pipeline import ocr_image
from app.extract.render import pixmap_to_bgr
from app.logging_config import get_logger

logger = get_logger("RasterPipeline")


def _open_document(document_bytes: bytes, filetype: str) -> fitz.Document:
    try:
        return fitz.open(stream=document_bytes, filetype=filetype)
    except Exception as exc:
        raise ValueError(f"Failed to open {filetype.upper()} document: {exc}") from exc


def sanitize_dpi_for_page_count(page_count: int) -> int:
    """Lower DPI on large documents — JPEG image-PDF stays small and fast."""
    if page_count > 150:
        return SANITIZE_DPI_LARGE
    if page_count > 50:
        return SANITIZE_DPI_MEDIUM
    return SANITIZE_DPI


def _pixmap_to_jpeg(pix: fitz.Pixmap, quality: int) -> bytes:
    gray = pix if pix.n == 1 else fitz.Pixmap(fitz.csGRAY, pix)
    try:
        return gray.tobytes("jpeg", jpg_quality=quality)
    except TypeError:
        return gray.tobytes(output="jpeg", jpg_quality=quality)


def _add_jpeg_page(clean_doc: fitz.Document, pix: fitz.Pixmap, dpi: int, jpeg: bytes) -> None:
    width_pt = pix.width * 72.0 / dpi
    height_pt = pix.height * 72.0 / dpi
    page = clean_doc.new_page(width=width_pt, height=height_pt)
    page.insert_image(page.rect, stream=jpeg)


def ocr_via_image_pdf_pipeline(
    document_bytes: bytes,
    filetype: str,
    lang: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Run the full sanitization pipeline page-by-page (memory safe).

    For each page:
      1. Rasterise source page to a grayscale image
      2. Embed the JPEG into a new image-only PDF (no font/text layer)
      3. OCR the same rasterised image
    """
    source = _open_document(document_bytes, filetype)
    clean_pdf = fitz.open()
    page_count = len(source)
    dpi = sanitize_dpi_for_page_count(page_count)
    fast_mode = is_fast_ocr_mode(page_count) or page_count > 1
    results: list[dict[str, Any]] = []

    try:
        if page_count == 0:
            raise ValueError(f"No pages found in {filetype.upper()} document.")

        logger.info(
            "Pipeline start: %s pages, sanitize_dpi=%s, jpeg_q=%s",
            page_count,
            dpi,
            SANITIZE_JPEG_QUALITY,
        )

        for index in range(page_count):
            if page_count >= 20 and index % 10 == 0:
                logger.info("Pipeline progress: page %s/%s", index + 1, page_count)

            pix = source.load_page(index).get_pixmap(
                dpi=dpi,
                alpha=False,
                colorspace=fitz.csGRAY,
            )
            jpeg = _pixmap_to_jpeg(pix, SANITIZE_JPEG_QUALITY)
            _add_jpeg_page(clean_pdf, pix, dpi, jpeg)

            image_bgr = pixmap_to_bgr(pix)
            results.append(
                ocr_image(image_bgr, lang, digital=True, fast=fast_mode)
            )

            del pix, jpeg, image_bgr
            gc.collect()

        pipeline_meta = {
            "pipeline": "pdf_to_image_to_pdf_to_ocr",
            "sanitized_image_pdf": True,
            "sanitize_dpi": dpi,
            "sanitize_jpeg_quality": SANITIZE_JPEG_QUALITY,
            "clean_pdf_pages": page_count,
        }
        return results, pipeline_meta
    finally:
        source.close()
        clean_pdf.close()
