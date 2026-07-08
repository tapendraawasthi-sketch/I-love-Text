"""Post-processing OCR results using LLM for accuracy enhancement."""

from __future__ import annotations

import logging
from typing import Any

from .nepali_language_model import get_nepali_language_model

logger = logging.getLogger(__name__)


def post_process_ocr_result(ocr_result: dict) -> dict:
    """
    Enhance OCR result using LLM if confidence is below threshold.

    Args:
        ocr_result: Output from app.ocr.engine.run_ocr_smart()
        {
            "text": extracted text,
            "mean_confidence": confidence score,
            "word_count": number of words,
            "lang_used": language code,
        }

    Returns:
        Enhanced OCR result with LLM corrections:
        {
            "text": corrected text,
            "mean_confidence": updated confidence,
            "word_count": updated word count,
            "lang_used": language code,
            "llm_enhanced": True/False,
            "llm_corrections": [...],
            "original_text": original OCR text,
        }
    """
    result = ocr_result.copy()
    confidence = result.get("mean_confidence", 0.5)

    # Only enhance if confidence is below threshold
    if confidence >= 0.80:
        result["llm_enhanced"] = False
        return result

    try:
        model = get_nepali_language_model()
        llm_result = model.correct_ocr_text(result["text"], confidence)

        if llm_result["corrections_made"]:
            result["text"] = llm_result["corrected"]
            result["mean_confidence"] = llm_result["confidence"]
            result["word_count"] = len(llm_result["corrected"].split())
            result["llm_enhanced"] = True
            result["llm_corrections"] = llm_result["corrections_made"]
            result["original_text"] = ocr_result["text"]
            logger.info(
                "LLM enhanced OCR: %d corrections, confidence: %.2f → %.2f",
                len(llm_result["corrections_made"]),
                confidence,
                llm_result["confidence"],
            )
        else:
            result["llm_enhanced"] = False
    except Exception as exc:
        logger.warning("LLM post-processing failed: %s", exc)
        result["llm_enhanced"] = False

    return result


def format_llm_enhanced_result(result: dict) -> str:
    """
    Format enhanced OCR result for display with correction metadata.

    Returns human-readable string with original and corrected text.
    """
    if not result.get("llm_enhanced"):
        return result["text"]

    lines = [
        "[LLM-Enhanced OCR Result]",
        f"Corrected Text: {result['text']}",
        f"Confidence: {result['mean_confidence']:.1%}",
    ]

    if result.get("llm_corrections"):
        lines.append("\nCorrections Made:")
        for correction in result["llm_corrections"]:
            lines.append(f"  - {correction}")

    return "\n".join(lines)
