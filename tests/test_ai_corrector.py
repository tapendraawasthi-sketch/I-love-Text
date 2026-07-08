from app.nlp.ai_corrector import (
    _is_garbled_repetition,
    _is_llm_output_acceptable,
)


def test_detects_repeated_nepali_words():
    garbled = "ऐन ऐन ऐन नियम नियम नियम संशोधन संशोधन संशोधन " * 20
    assert _is_garbled_repetition(garbled) is True


def test_accepts_normal_nepali_text():
    normal = (
        "नेपाल सरकारको मन्त्रिपरिषद्ले यो ऐन बनाएको छ। "
        "यस ऐनको उद्देश्य आयकर सम्बन्धी व्यवस्था मिलाउनु हो।"
    )
    assert _is_garbled_repetition(normal) is False


def test_rejects_llm_output_worse_than_original():
    original = "नेपाल सरकारको मन्त्रिपरिषद्ले यो ऐन बनाएको छ।"
    garbled = "ऐन ऐन ऐन नियम नियम नियम संशोधन संशोधन संशोधन " * 10
    assert _is_llm_output_acceptable(garbled, original) is False


def test_accepts_minor_llm_cleanup():
    original = "नेपाल सरकारको मन्त्रिपरिषद्ले यो ऐन बनाएको छ।"
    cleaned = "नेपाल सरकारको मन्त्रिपरिषद्ले यो ऐन बनाएको छ।"
    assert _is_llm_output_acceptable(cleaned, original) is True
