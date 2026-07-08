"""Integration bridge between OCR pipeline and LLM enhancement."""

from __future__ import annotations

import logging
from typing import Any

from app.llm.llm_postprocess import post_process_ocr_result

logger = logging.getLogger(__name__)


def enhance_ocr_with_llm(ocr_result: dict, enable_enhancement: bool = True) -> dict:
    """
    Optionally enhance OCR results with LLM for improved Nepali accuracy.

    This is called after run_ocr_smart() to improve results when confidence is low.

    Args:
        ocr_result: Raw OCR output from engine.run_ocr_smart()
        enable_enhancement: Whether to apply LLM enhancement (default: True)

    Returns:
        Enhanced OCR result (or original if enhancement disabled)
    """
    if not enable_enhancement:
        return ocr_result

    return post_process_ocr_result(ocr_result)
