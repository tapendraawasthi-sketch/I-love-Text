"""
Parallel OCR helpers for multi-page documents.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

import numpy as np

from app.config import OCR_PAGE_WORKERS
from app.extract.ocr_pipeline import ocr_image


def ocr_page_images(
    page_images: list[np.ndarray],
    lang: str,
    *,
    digital: bool,
) -> list[dict[str, Any]]:
    """OCR multiple page images, using parallel workers when beneficial."""
    if not page_images:
        return []

    if len(page_images) == 1 or OCR_PAGE_WORKERS <= 1:
        return [ocr_image(image, lang, digital=digital) for image in page_images]

    workers = min(OCR_PAGE_WORKERS, len(page_images))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [
            executor.submit(ocr_image, image, lang, digital=digital)
            for image in page_images
        ]
        return [future.result() for future in futures]
