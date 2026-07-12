"""
Regression test for unifying the correction vocabulary used by
repair_corrupted_devanagari() with app.intelligence.nepal_knowledge_base,
and for gating substitutions on confidence.

Previously, repair_corrupted_devanagari() matched genuinely-corrupted words
against its own tiny, hard-coded 28-word DOMAIN_LEXICON (legal/gazette
register only). A genuinely corrupted accounting/banking/common word (e.g.
"राजस्व", "आय") had no chance of resolving correctly since those words
weren't even in the lexicon it searched -- it would either fail to match at
all or, worse, get force-matched to the nearest legal-register word purely
by coincidental edit distance.
"""
from __future__ import annotations

from app.nlp.nepali_sentence_intelligence import repair_corrupted_devanagari


def test_genuinely_corrupted_accounting_term_resolves_correctly():
    # "राजस्व" (revenue) is in ACCOUNTING_TERMS in nepal_knowledge_base.py
    # but was NOT in the old 28-word DOMAIN_LEXICON.
    text = "कम्पनीको रा\ue000जस्व बढेको छ"
    repaired = repair_corrupted_devanagari(text, domain="accounting")
    assert "राजस्व" in repaired
    assert "\ue000" not in repaired


def test_genuinely_corrupted_legal_term_still_resolves():
    text = "यो ऐ\ue000न हो"
    repaired = repair_corrupted_devanagari(text, domain="legal")
    assert "ऐन" in repaired


def test_domain_none_matches_across_full_vocabulary():
    text = "कम्पनीको रा\ue000जस्व बढेको छ"
    repaired = repair_corrupted_devanagari(text)  # no domain restriction
    assert "राजस्व" in repaired


def test_low_confidence_corrupted_word_is_left_as_marker_stripped_original():
    """
    A corrupted word with no close match in any vocabulary should not be
    force-replaced with an unrelated word -- it should come back as the
    marker-stripped original instead.
    """
    # A long, distinctive, made-up "word" that shouldn't fuzzy-match
    # anything in the knowledge base once its corruption marker is
    # stripped.
    text = "यो झ्याइँकुट्टेधचक्कमक्क\ue000थ्याम भन्ने शब्द हो"
    repaired = repair_corrupted_devanagari(text)
    assert "\ue000" not in repaired
    assert "झ्याइँकुट्टेधचक्कमक्कथ्याम" in repaired
