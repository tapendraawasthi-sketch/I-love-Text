from app.extract.page_ocr import ocr_page_images
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


def test_ocr_page_images_processes_all_pages_when_single_worker(monkeypatch):
    calls: list[int] = []

    def fake_ocr(image, lang, digital):
        calls.append(1)
        return {
            "text": f"page-{len(calls)}",
            "mean_confidence": 90.0,
            "word_count": 2,
            "lang_used": lang,
        }

    monkeypatch.setattr("app.extract.page_ocr.OCR_PAGE_WORKERS", 1)
    monkeypatch.setattr("app.extract.page_ocr.ocr_image", fake_ocr)

    import numpy as np

    images = [np.zeros((10, 10), dtype=np.uint8) for _ in range(3)]
    results = ocr_page_images(images, "nep", digital=True)

    assert len(results) == 3
    assert calls == [1, 1, 1]
