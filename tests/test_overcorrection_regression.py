"""
Regression test for a critical overcorrection bug in
repair_corrupted_devanagari(), found via forensic audit of a real
production output file (an annual government revenue report).

BUG: the function used to run its ~28-word DOMAIN_LEXICON distance-match
against *every* Devanagari word in the entire text, as soon as the
caller-side gate (`corruption_score(text) > 0`) tripped anywhere in a
large text block -- even from a single stray OCR-garbage character. Since
`corruption_score` is a proportion (corrupted chars / text length), one
stray corruption marker in an otherwise-clean multi-page document was
enough to trigger force-replacement of hundreds of unrelated real words
(names, place names, English loanword transliterations, technical terms)
with whichever DOMAIN_LEXICON entry happened to be nearest by consonant-
skeleton edit distance. In the audited real document this produced
widespread nonsensical substitutions such as:

  - "बिजनेस प्रोसेस रि-इन्जिनियरिङ" (Business Process Reengineering)
    -> "प्रोसेस" silently replaced with "परिभाषा" (a DOMAIN_LEXICON word)
  - Real personal/place names (e.g. "जनकराज अर्याल", "कृष्णनगर") getting
    "नियम"/"मिति"/"नाम" filler words inserted around or in place of them
  - "फे फैसला" instead of "फेसलेस" (Faceless)

FIX: the lexicon distance-match is now only applied to a word that itself
contained an actual corruption marker character (private-use-area,
replacement char, or geometric-shape OCR-garbage placeholder) BEFORE that
marker was stripped. Every other word in the same text block passes
through completely unchanged, regardless of the document-level
corruption_score.
"""
from __future__ import annotations

from app.nlp.nepali_sentence_intelligence import (
    corruption_score,
    repair_corrupted_devanagari,
)


def test_clean_words_survive_when_a_single_stray_marker_triggers_repair():
    """
    The exact failure mode from the real-document audit: a large, mostly
    clean text block containing real proper nouns and English loanword
    transliterations, with a single stray OCR corruption marker somewhere
    in it. Only the marker itself should be removed; everything else must
    survive byte-for-byte.
    """
    real_words_should_survive = (
        "बिजनेस प्रोसेस रि-इन्जिनियरिङ जनकराज अर्याल आचार्य कृष्णनगर "
        "काठमाडौँ नेपालगन्ज भक्तपुर विराटनगर"
    )
    text = real_words_should_survive + " \ue000 " + "थप साधारण पाठ यहाँ छ जुन सफा हुनुपर्छ"

    assert corruption_score(text) > 0  # confirm this text WOULD have tripped the old bug

    repaired = repair_corrupted_devanagari(text)

    for word in real_words_should_survive.split():
        assert word in repaired, f"{word!r} was altered but should have passed through untouched"

    # The actual corruption marker itself must be gone.
    assert "\ue000" not in repaired


def test_word_actually_containing_a_corruption_marker_is_still_repaired():
    """A word that itself contains a corruption marker should still be
    cleaned up / lexicon-matched, since it genuinely is broken."""
    text = "यो ऐ\ue000न हो"
    repaired = repair_corrupted_devanagari(text)
    assert "\ue000" not in repaired
    assert "ऐन" in repaired


def test_no_corruption_marker_means_no_change_at_all():
    text = "यो एउटा सामान्य वाक्य हो जसमा कुनै त्रुटि छैन"
    assert corruption_score(text) == 0.0
    # repair_corrupted_devanagari isn't even called by real callers when
    # corruption_score == 0, but it should be a safe no-op regardless.
    assert repair_corrupted_devanagari(text) == text
