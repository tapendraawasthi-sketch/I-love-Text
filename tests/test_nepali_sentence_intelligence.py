from app.nlp.nepali_sentence_intelligence import (
    analyze_sentence_meaning,
    repair_corrupted_devanagari,
    synthesize_sentence_context,
)


def test_repair_legal_niyamawali():
    corrupt = (
        "पेट्रोलियम उद्योग (आयकर) \u25A1\u25A1ियमव\u25A1\u25A1, २०४१। "
        "नेपाल \u25A1\u25A1\u25A1पत्रमा प्रकाशित \u25A1\u25A1\u25A1ति : २०४१।१२।९"
    )
    repaired = repair_corrupted_devanagari(corrupt)
    assert "नियमवाली" in repaired
    assert "राजपत्र" in repaired
    assert "मिति" in repaired


def test_sentence_context_legal():
    text = "आयकर ऐन, २०३१ को दफा ६५ बमोजिम पेट्रोलियम उद्योग (आयकर) नियमवाली, २०४१।"
    ctx = synthesize_sentence_context(text)
    assert "legal" in ctx.lower() or "Legal" in ctx


def test_analyze_meaning_repairs_corruption():
    corrupt = "नेपाल \u25A1पत्रमा प्रकाशित"
    m = analyze_sentence_meaning(corrupt)
    assert "राजपत्र" in m.repaired_text
