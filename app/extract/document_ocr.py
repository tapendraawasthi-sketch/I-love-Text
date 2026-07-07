"""
Document-level OCR with memory-safe page processing for cloud hosting.
"""
from __future__ import annotations

import gc
from typing import Any

import fitz

from app.config import (
    HIGH_DPI_RETRY_MAX_PAGES,
    OCR_PAGE_WORKERS,
    PARALLEL_PAGE_THRESHOLD,
    PDF_RENDER_DPI_HIGH,
    PDF_RETRY_CONFIDENCE,
    is_fast_ocr_mode,
    render_dpi_for_page_count,
)
from app.extract.ocr_pipeline import ocr_image, should_retry_page
from app.extract.page_ocr import ocr_page_images
from app.extract.render import render_page
from app.logging_config import get_logger
from app.ocr.engine import score_result_dict

logger = get_logger("DocumentOCR")


def _open_document(document_bytes: bytes, filetype: str) -> fitz.Document:
    try:
        return fitz.open(stream=document_bytes, filetype=filetype)
    except Exception as exc:
        raise ValueError(f"Failed to open {filetype.upper()} document: {exc}") from exc


def _ocr_single_page(
    doc: fitz.Document,
    page_index: int,
    lang: str,
    *,
    digital: bool,
    allow_high_res: bool,
    render_dpi: int,
    fast_mode: bool,
) -> dict[str, Any]:
    page_image = render_page(doc.load_page(page_index), render_dpi)
    try:
        result = ocr_image(page_image, lang, digital=digital, fast=fast_mode)

        weak = (
            allow_high_res
            and not fast_mode
            and (
                should_retry_page(result)
                or result["mean_confidence"] < PDF_RETRY_CONFIDENCE
            )
        )
        if not weak:
            return result

        high_res_image = render_page(doc.load_page(page_index), PDF_RENDER_DPI_HIGH)
        try:
            retry = ocr_image(high_res_image, lang, digital=digital, fast=False)
        finally:
            del high_res_image

        if score_result_dict(retry) > score_result_dict(result):
            return retry
        return result
    finally:
        del page_image


def ocr_document(
    document_bytes: bytes,
    filetype: str,
    lang: str,
    *,
    digital: bool = True,
) -> list[dict[str, Any]]:
    """
    OCR every page in a document.

    Processes pages incrementally to stay within cloud memory limits.
    No page-count cap — large files use a faster OCR path automatically.
    """
    doc = _open_document(document_bytes, filetype)
    try:
        page_count = len(doc)
        if page_count == 0:
            raise ValueError(f"No pages found in {filetype.upper()} document.")

        render_dpi = render_dpi_for_page_count(page_count)
        fast_mode = is_fast_ocr_mode(page_count)
        allow_high_res = page_count <= HIGH_DPI_RETRY_MAX_PAGES and not fast_mode
        use_parallel = (
            page_count <= PARALLEL_PAGE_THRESHOLD
            and OCR_PAGE_WORKERS > 1
        )

        logger.info(
            "OCR start: %s pages, dpi=%s, parallel=%s, fast=%s",
            page_count,
            render_dpi,
            use_parallel,
            fast_mode,
        )

        if use_parallel:
            page_images = [
                render_page(doc.load_page(index), render_dpi)
                for index in range(page_count)
            ]
            try:
                return ocr_page_images(
                    page_images,
                    lang,
                    digital=digital,
                    fast=fast_mode,
                )
            finally:
                del page_images
                gc.collect()

        results: list[dict[str, Any]] = []
        for index in range(page_count):
            if page_count >= 20 and index % 10 == 0:
                logger.info("OCR progress: page %s/%s", index + 1, page_count)
            results.append(
                _ocr_single_page(
                    doc,
                    index,
                    lang,
                    digital=digital,
                    allow_high_res=allow_high_res,
                    render_dpi=render_dpi,
                    fast_mode=fast_mode,
                )
            )
            gc.collect()

        return results
    finally:
        doc.close()


def format_page_results(page_results: list[dict[str, Any]]) -> dict[str, Any]:
    page_texts = [result["text"] for result in page_results]
    confidences = [result["mean_confidence"] for result in page_results]

    if len(page_texts) == 1:
        final_text = page_texts[0]
    else:
        final_text = "\n\n--- Page Break ---\n\n".join(page_texts)

    mean_confidence = (
        round(sum(confidences) / len(confidences), 2) if confidences else 0.0
    )

    return {
        "text": final_text,
        "pages": len(page_results),
        "method_per_page": ["image_ocr"] * len(page_results),
        "method": "image_ocr",
        "mean_confidence": mean_confidence,
        "had_legacy_fonts": False,
        "detected_fonts": [],
    }
