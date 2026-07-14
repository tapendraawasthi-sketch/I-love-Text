"""
Tesseract OCR engine wrapper with layout-aware Nepali extraction.
"""
from __future__ import annotations

import re

import pytesseract
import numpy as np

from app.config import (
    COLUMN_GAP_RATIO,
    DEFAULT_OCR_CONFIG,
    OCR_GOOD_CONFIDENCE,
    OCR_MIN_WORDS,
    OCR_PSM_PRIMARY,
    OCR_PSM_RETRY,
    configure_tesseract,
)
from app.logging_config import get_logger
from app.ocr.layout import reconstruct_layout_from_data
from app.ocr.nepali_postprocess import (
    normalize_ocr_content,
    detect_content_script,
)

logger = get_logger("OCREngine")

configure_tesseract()

_DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]")


def available_languages() -> list[str]:
    """Returns installed Tesseract language packs."""
    try:
        return pytesseract.get_languages(config="")
    except Exception:
        return []


def resolve_ocr_lang(lang: str) -> str:
    """
    Resolve OCR language quickly for Nepali-first workloads.

    Auto mode prefers nep+eng directly instead of sweeping every language pack.
    """
    langs_available = set(available_languages())

    if lang == "auto":
        if "nep" in langs_available and "eng" in langs_available:
            return "nep+eng"
        if "nep" in langs_available:
            return "nep"
        if "eng" in langs_available:
            return "eng"
        return "eng"

    if lang == "nep" and "nep" in langs_available and "eng" in langs_available:
        return "nep+eng"

    return lang


def _build_config(psm: int) -> str:
    return (
        f"--oem 1 --psm {psm} "
        "-c preserve_interword_spaces=1 "
        "-c textord_tabfind_find_tables=1 "
        "-c textord_heavy_nr=1"
    )


def _devanagari_ratio(text: str) -> float:
    letters = [char for char in text if char.isalpha()]
    if not letters:
        return 0.0
    devanagari = sum(1 for char in letters if _DEVANAGARI_RE.match(char))
    return devanagari / len(letters)


def score_ocr_result(text: str, mean_confidence: float, word_count: int) -> float:
    if not text.strip():
        return -1.0

    score = mean_confidence * min(word_count, 80) / 80.0
    ratio = _devanagari_ratio(text)
    if ratio >= 0.25:
        score *= 1.0 + min(ratio, 0.8) * 0.25
    return score


def score_result_dict(result: dict) -> float:
    return score_ocr_result(
        result["text"],
        result["mean_confidence"],
        result["word_count"],
    )


def _score_result(result: dict) -> float:
    return score_result_dict(result)


def _good_enough(result: dict) -> bool:
    return (
        result["mean_confidence"] >= OCR_GOOD_CONFIDENCE
        and result["word_count"] >= OCR_MIN_WORDS
        and bool(result["text"].strip())
    )


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
    """Run Tesseract on an image and return layout-aware structured results."""
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
        min_confidence=40.0,
    )
    mean_conf, word_count = _mean_confidence(data)
    char_confidences = [
        float(data["conf"][i])
        for i in range(len(data["conf"]))
        if float(data["conf"][i]) >= 0 and str(data["text"][i]).strip()
    ]

    normalized = normalize_ocr_content(layout_text)

    return {
        "text": normalized,
        "mean_confidence": round(mean_conf, 2),
        "ocr_confidence": round(mean_conf, 2),
        "word_count": word_count,
        "lang_used": lang,
        "char_confidences": char_confidences,
        "content_script": detect_content_script(normalized),
    }


def run_ocr_smart(image: np.ndarray, lang: str, *, fast: bool = False) -> dict:
    """
    Fast OCR with selective retries.

    Runs one primary pass first, then only retries alternate PSM modes when
    confidence or word count is weak. In fast mode, uses a single pass only.
    """
    resolved_lang = resolve_ocr_lang(lang)
    result = run_ocr(
        image,
        resolved_lang,
        config=_build_config(OCR_PSM_PRIMARY),
    )

    if fast or _good_enough(result):
        return result

    best = result
    best_score = _score_result(result)

    for psm in OCR_PSM_RETRY:
        try:
            candidate = run_ocr(
                image,
                resolved_lang,
                config=_build_config(psm),
            )
        except Exception:
            continue

        score = _score_result(candidate)
        if score > best_score:
            best = candidate
            best_score = score

        if _good_enough(best):
            break

    return best


def run_ocr_block(
    image: np.ndarray,
    lang: str = "auto",
    *,
    block_meta: dict | None = None,
    fast: bool = False,
) -> dict:
    """
    OCR a single image-ink block with language resolution and confidence.
    """
    resolved = _resolve_block_ocr_lang(lang, block_meta)
    result = run_ocr_smart(image, resolved, fast=fast)
    result["ocr_confidence"] = result.get("mean_confidence", 0.0)
    result["lang_used"] = resolved
    return result


def _resolve_block_ocr_lang(
    lang_hint: str,
    block_meta: dict | None = None,
) -> str:
    """Resolve Tesseract language for Nepali / English / mixed blocks."""
    meta = block_meta or {}
    language = meta.get("language")

    if lang_hint and lang_hint not in ("auto", ""):
        return resolve_ocr_lang(lang_hint)
    if language == "nep":
        return resolve_ocr_lang("nep")
    if language == "eng":
        return resolve_ocr_lang("eng")
    return resolve_ocr_lang("nep+eng")
