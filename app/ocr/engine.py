"""
Tesseract OCR engine wrapper with layout-aware Nepali extraction.
"""
from __future__ import annotations

import pytesseract
import numpy as np

from app.config import (
    COLUMN_GAP_RATIO,
    DEFAULT_OCR_CONFIG,
    OCR_PSM_CANDIDATES,
    configure_tesseract,
)
from app.logging_config import get_logger
from app.ocr.layout import reconstruct_layout_from_data

logger = get_logger("OCREngine")

configure_tesseract()


def available_languages() -> list[str]:
    """Returns installed Tesseract language packs."""
    try:
        return pytesseract.get_languages(config="")
    except Exception:
        return []


def _build_config(psm: int) -> str:
    return (
        f"--oem 1 --psm {psm} "
        "-c preserve_interword_spaces=1 "
        "-c textord_tabfind_find_tables=1"
    )


def _score_result(text: str, mean_confidence: float, word_count: int) -> float:
    if not text.strip():
        return -1.0
    return mean_confidence * min(word_count, 80) / 80.0


def _mean_confidence(data: dict) -> tuple[float, int]:
    confidences = [
        float(data["conf"][i])
        for i in range(len(data["conf"]))
        if float(data["conf"][i]) >= 0 and str(data["text"][i]).strip()
    ]
    if not confidences:
        return 0.0, 0
    return sum(confidences) / len(confidences), len(confidences)


def run_ocr(image: np.ndarray, lang: str, config: str | None = None) -> dict:
    """
    Run Tesseract on an image and return layout-aware structured results.
    """
    if config is None:
        config = DEFAULT_OCR_CONFIG

    try:
        data = pytesseract.image_to_data(
            image,
            lang=lang,
            config=config,
            output_type=pytesseract.Output.DICT,
        )
    except Exception as exc:
        raise RuntimeError(
            "OCR failed. Ensure tesseract-ocr and lang packs are installed. "
            f"Error: {exc}"
        ) from exc

    layout_text = reconstruct_layout_from_data(
        data,
        column_gap_ratio=COLUMN_GAP_RATIO,
    )
    mean_conf, word_count = _mean_confidence(data)

    if not layout_text and "--psm 3" in config:
        fallback_config = config.replace("--psm 3", "--psm 6")
        return run_ocr(image, lang=lang, config=fallback_config)

    return {
        "text": layout_text,
        "mean_confidence": round(mean_conf, 2),
        "word_count": word_count,
        "lang_used": lang,
    }


def run_ocr_best_psm(image: np.ndarray, lang: str) -> dict:
    """Try multiple page-segmentation modes and keep the strongest result."""
    best_result: dict | None = None
    best_score = -1.0

    for psm in OCR_PSM_CANDIDATES:
        try:
            result = run_ocr(image, lang=lang, config=_build_config(psm))
        except Exception:
            continue

        score = _score_result(
            result["text"],
            result["mean_confidence"],
            result["word_count"],
        )
        if score > best_score:
            best_score = score
            best_result = result

    if best_result is None:
        return {
            "text": "",
            "mean_confidence": 0.0,
            "word_count": 0,
            "lang_used": lang,
        }

    return best_result


def ocr_with_best_lang(image: np.ndarray) -> dict:
    """Try multiple language combinations and return the best result."""
    langs_available = available_languages()

    candidates: list[str] = []
    if "nep" in langs_available:
        candidates.append("nep")
    if "eng" in langs_available:
        candidates.append("eng")
    if "nep" in langs_available and "eng" in langs_available:
        candidates.append("nep+eng")

    if not candidates:
        candidates = ["eng"]

    best_result: dict | None = None
    best_score = -1.0

    for lang in candidates:
        try:
            result = run_ocr_best_psm(image, lang=lang)
        except Exception:
            continue

        score = _score_result(
            result["text"],
            result["mean_confidence"],
            result["word_count"],
        )
        if score > best_score:
            best_score = score
            best_result = result

    if best_result is None:
        return {
            "text": "",
            "mean_confidence": 0.0,
            "word_count": 0,
            "lang_used": "none",
        }

    return best_result
