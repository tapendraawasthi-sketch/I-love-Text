"""
Shared image-first OCR pipeline used by PDF, DOCX, and image extractors.
"""
from __future__ import annotations

import numpy as np

from app.ocr.engine import ocr_with_best_lang, run_ocr_best_psm
from app.ocr.preprocess import preprocess_for_ocr


def ocr_image(image_bgr: np.ndarray, lang: str, *, digital: bool) -> dict:
    clean_img = preprocess_for_ocr(image_bgr, aggressive=False, digital=digital)
    if lang == "auto":
        result = ocr_with_best_lang(clean_img)
    else:
        result = run_ocr_best_psm(clean_img, lang)

    if 0 < result["mean_confidence"] < 55:
        retry_img = preprocess_for_ocr(image_bgr, aggressive=True, digital=False)
        if lang == "auto":
            retry = ocr_with_best_lang(retry_img)
        else:
            retry = run_ocr_best_psm(retry_img, lang)
        if retry["mean_confidence"] > result["mean_confidence"]:
            result = retry

    return result
