"""
Spatial layout reconstruction from Tesseract word bounding boxes.
"""
from __future__ import annotations

import re
from typing import Any

_NOISE_RE = re.compile(r"^[_|\[\]\\\/=\-]+$")
_DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]")


def _word_bbox(data: dict[str, Any], index: int) -> dict[str, float | str]:
    left = int(data["left"][index])
    top = int(data["top"][index])
    width = int(data["width"][index])
    height = int(data["height"][index])
    return {
        "text": data["text"][index].strip(),
        "conf": float(data["conf"][index]),
        "x0": left,
        "y0": top,
        "x1": left + width,
        "y1": top + height,
    }


def _contains_devanagari(text: str) -> bool:
    return bool(_DEVANAGARI_RE.search(text))


def _effective_min_confidence(text: str, min_confidence: float) -> float:
    """
    Nepali OCR often reports lower confidence for valid Devanagari tokens.
    """
    if _contains_devanagari(text):
        return max(28.0, min_confidence - 15.0)
    return min_confidence


def _is_noise_word(word: dict[str, float | str], min_confidence: float) -> bool:
    text = str(word["text"])
    conf = float(word["conf"])
    threshold = _effective_min_confidence(text, min_confidence)

    if not text:
        return True
    if _NOISE_RE.match(text):
        return True
    if conf < threshold and len(text) < 3:
        return True
    if (
        not _contains_devanagari(text)
        and conf < 60
        and re.fullmatch(r"[a-zA-Z&@#]+", text)
        and len(text) < 4
    ):
        return True
    return False


def extract_words(
    data: dict[str, Any],
    *,
    min_confidence: float = 45.0,
) -> list[dict[str, float | str]]:
    words: list[dict[str, float | str]] = []
    for i in range(len(data.get("text", []))):
        word = _word_bbox(data, i)
        if _is_noise_word(word, min_confidence):
            continue
        words.append(word)
    return words


def group_words_into_rows(
    words: list[dict[str, float | str]],
    *,
    row_overlap_ratio: float = 0.6,
) -> list[list[dict[str, float | str]]]:
    if not words:
        return []

    sorted_words = sorted(words, key=lambda w: (float(w["y0"]), float(w["x0"])))
    rows: list[list[dict[str, float | str]]] = []
    current_row: list[dict[str, float | str]] = []

    for word in sorted_words:
        if not current_row:
            current_row = [word]
            continue

        prev = current_row[-1]
        prev_height = max(1.0, float(prev["y1"]) - float(prev["y0"]))
        cy_word = (float(word["y0"]) + float(word["y1"])) / 2.0
        cy_prev = (float(prev["y0"]) + float(prev["y1"])) / 2.0

        if abs(cy_word - cy_prev) < prev_height * row_overlap_ratio:
            current_row.append(word)
        else:
            rows.append(current_row)
            current_row = [word]

    if current_row:
        rows.append(current_row)

    return rows


def format_rows_as_text(
    rows: list[list[dict[str, float | str]]],
    *,
    column_gap_ratio: float = 1.5,
) -> str:
    lines: list[str] = []

    for row in rows:
        row.sort(key=lambda w: float(w["x0"]))
        parts: list[str] = []

        for i, word in enumerate(row):
            text = str(word["text"])
            if i == 0:
                parts.append(text)
                continue

            prev = row[i - 1]
            gap = float(word["x0"]) - float(prev["x1"])
            prev_height = max(1.0, float(prev["y1"]) - float(prev["y0"]))

            if gap > prev_height * column_gap_ratio:
                parts.append("\t" + text)
            else:
                parts.append(" " + text)

        lines.append("".join(parts))

    return "\n".join(lines).strip()


def reconstruct_layout_from_data(
    data: dict[str, Any],
    *,
    min_confidence: float = 45.0,
    column_gap_ratio: float = 1.5,
    row_overlap_ratio: float = 0.6,
) -> str:
    words = extract_words(data, min_confidence=min_confidence)
    rows = group_words_into_rows(words, row_overlap_ratio=row_overlap_ratio)
    return format_rows_as_text(rows, column_gap_ratio=column_gap_ratio)
