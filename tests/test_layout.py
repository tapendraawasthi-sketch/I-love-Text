import pytest

from app.ocr.layout import (
    extract_words,
    format_rows_as_text,
    group_words_into_rows,
    reconstruct_layout_from_data,
)


def _sample_data():
    return {
        "text": ["Name", "Age", "राम", "२५", "सीता", "३०", "|", "_"],
        "conf": [95, 94, 92, 90, 91, 89, 30, 20],
        "left": [50, 220, 50, 220, 50, 220, 10, 400],
        "top": [100, 100, 140, 140, 180, 180, 50, 50],
        "width": [60, 40, 50, 30, 55, 30, 5, 20],
        "height": [20, 20, 20, 20, 20, 20, 200, 5],
    }


def test_extract_words_filters_noise():
    words = extract_words(_sample_data())
    texts = [str(word["text"]) for word in words]
    assert "Name" in texts
    assert "राम" in texts
    assert "|" not in texts
    assert "_" not in texts


def test_group_words_into_rows():
    words = extract_words(_sample_data())
    rows = group_words_into_rows(words)
    assert len(rows) == 3
    assert [str(word["text"]) for word in rows[0]] == ["Name", "Age"]
    assert [str(word["text"]) for word in rows[1]] == ["राम", "२५"]


def test_format_rows_as_text_uses_tabs_for_columns():
    words = extract_words(_sample_data())
    rows = group_words_into_rows(words)
    text = format_rows_as_text(rows)
    assert "Name\tAge" in text
    assert "राम\t२५" in text
    assert "सीता\t३०" in text


def test_reconstruct_layout_from_data():
    text = reconstruct_layout_from_data(_sample_data())
    assert "Name\tAge" in text
    assert "राम\t२५" in text
