"""
Shared image-first OCR pipeline used by PDF, DOCX, and image extractors.
"""
from __future__ import annotations

import numpy as np

from app.config import OCR_RETRY_CONFIDENCE
from app.ocr.engine import run_ocr_smart, score_result_dict
from app.ocr.preprocess import preprocess_for_ocr


def should_retry_page(result: dict) -> bool:
    return (
        not result["text"].strip()
        or result["word_count"] < 2
        or result["mean_confidence"] < OCR_RETRY_CONFIDENCE
    )


def ocr_image(
    image_bgr: np.ndarray,
    lang: str,
    *,
    digital: bool,
    fast: bool = False,
) -> dict:
    clean_img = preprocess_for_ocr(image_bgr, aggressive=False, digital=digital)
    result = run_ocr_smart(clean_img, lang, fast=fast)

    if fast or not should_retry_page(result):
        return result

    retry_img = preprocess_for_ocr(
        image_bgr,
        aggressive=not digital,
        digital=False,
    )
    retry = run_ocr_smart(retry_img, lang, fast=fast)

    if score_result_dict(retry) > score_result_dict(result):
        return retry

    return result
