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
    SANITIZE_MAX_JPEG_BYTES,
    is_fast_ocr_mode,
)
from app.extract.ocr_pipeline import ocr_image
from app.extract.page_raster import adaptive_jpeg_quality, build_image_only_pdf_page
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


def ocr_via_image_pdf_pipeline(
    document_bytes: bytes,
    filetype: str,
    lang: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Run the full sanitization pipeline page-by-page (memory safe).

    For each page:
      1. Rasterise source page to a grayscale image
      2. Compress JPEG (text-safe adaptive quality)
      3. Embed into image-only PDF
      4. OCR the same rasterised image
    """
    source = _open_document(document_bytes, filetype)
    clean_pdf = fitz.open()
    page_count = len(source)
    dpi = sanitize_dpi_for_page_count(page_count)
    size_mb = len(document_bytes) / (1024 * 1024)
    max_jpeg = 650_000 if size_mb > 70 else SANITIZE_MAX_JPEG_BYTES
    fast_mode = is_fast_ocr_mode(page_count) or page_count > 1
    results: list[dict[str, Any]] = []
    used_qualities: list[int] = []

    try:
        if page_count == 0:
            raise ValueError(f"No pages found in {filetype.upper()} document.")

        logger.info(
            "Pipeline start: %s pages, sanitize_dpi=%s, max_jpeg_kb=%s",
            page_count,
            dpi,
            max_jpeg // 1024,
        )

        for index in range(page_count):
            if page_count >= 20 and index % 10 == 0:
                logger.info("Pipeline progress: page %s/%s", index + 1, page_count)

            pix = source.load_page(index).get_pixmap(
                dpi=dpi,
                alpha=False,
                colorspace=fitz.csGRAY,
            )
            jpeg, quality = adaptive_jpeg_quality(pix, max_bytes=max_jpeg)
            used_qualities.append(quality)
            build_image_only_pdf_page(clean_pdf, pix, dpi, jpeg)

            image_bgr = pixmap_to_bgr(pix)
            results.append(
                ocr_image(image_bgr, lang, digital=True, fast=fast_mode)
            )

            del pix, jpeg, image_bgr
            gc.collect()

        avg_q = round(sum(used_qualities) / len(used_qualities), 1) if used_qualities else 0
        pipeline_meta = {
            "pipeline": "pdf_to_image_to_pdf_to_ocr",
            "sanitized_image_pdf": True,
            "sanitize_dpi": dpi,
            "avg_jpeg_quality": avg_q,
            "clean_pdf_pages": page_count,
            "file_size_mb": round(size_mb, 2),
        }
        return results, pipeline_meta
    finally:
        source.close()
        clean_pdf.close()
