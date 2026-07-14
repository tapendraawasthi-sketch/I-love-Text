from app.ocr.engine import resolve_ocr_lang, score_result_dict
from app.ocr.layout import extract_words


def test_resolve_ocr_lang_prefers_nep_plus_eng(monkeypatch):
    monkeypatch.setattr(
        "app.ocr.engine.available_languages",
        lambda: ["eng", "nep", "osd"],
    )
    assert resolve_ocr_lang("auto") == "nep+eng"
    assert resolve_ocr_lang("nep") == "nep+eng"


def test_score_result_dict_prefers_devanagari():
    nepali = {
        "text": "नेपाल सरकारको निर्णय",
        "mean_confidence": 72.0,
        "word_count": 8,
    }
    ascii_text = {
        "text": "random ascii words",
        "mean_confidence": 72.0,
        "word_count": 8,
    }
    assert score_result_dict(nepali) > score_result_dict(ascii_text)


def test_extract_words_keeps_low_confidence_devanagari():
    data = {
        "text": ["नेपाल", "xx", "राम"],
        "conf": [38, 20, 36],
        "left": [10, 80, 140],
        "top": [10, 10, 10],
        "width": [40, 20, 30],
        "height": [18, 18, 18],
    }
    words = extract_words(data, min_confidence=45.0)
    texts = [str(word["text"]) for word in words]
    assert "नेपाल" in texts
    assert "राम" in texts
    assert "xx" not in texts
