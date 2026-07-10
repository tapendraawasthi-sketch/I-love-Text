# In the validate_and_repair_word function, replace the hardcoded _COMMON_WORDS
# lookup with the knowledge base:

def validate_and_repair_word(word: str) -> tuple[str, float, str]:
    """
    Validate and repair a single Devanagari word.
    Now uses the Nepal Knowledge Base for correction.
    """
    if not word or not _DEVANAGARI_RE.search(word):
        return word, 100.0, ""

    # Check against knowledge base (much larger vocabulary)
    try:
        from app.intelligence.nepal_knowledge_base import (
            is_known_word, correct_word,
        )

        if is_known_word(word):
            return word, 100.0, ""

        # Validate Unicode sequences
        result = validate_devanagari_text(word)
        if result.is_valid:
            return word, 95.0, ""

        # Try knowledge base correction
        corrected, conf, source = correct_word(word)
        if corrected != word and conf >= 65:
            return corrected, conf, f"kb_{source}"

        # Try repair
        repaired = result.repaired_text
        if is_known_word(repaired):
            return repaired, 90.0, "repaired_to_known_word"

        if repaired != word:
            return repaired, 70.0, "sequence_repaired"

        return word, 60.0, "unvalidated"

    except ImportError:
        # Fallback if knowledge base not available
        if word in _COMMON_WORDS:
            return word, 100.0, ""

        result = validate_devanagari_text(word)
        if result.is_valid:
            return word, 95.0, ""

        repaired = result.repaired_text
        if repaired in _COMMON_WORDS:
            return repaired, 90.0, "repaired_to_known_word"

        if repaired != word:
            return repaired, 70.0, "sequence_repaired"

        return word, 60.0, "unvalidated"
