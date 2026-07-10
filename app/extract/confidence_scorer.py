from typing import Any

def score_multi_dimensional(
    text: str,
    page_number: int = 0,
    domain: str = "general",
) -> dict[str, Any]:
    """
    Produce multi-dimensional confidence scores.

    Returns confidence at character, word, line, paragraph, and page level.
    This addresses Problem 18 — human confidence zones.
    """
    import re

    if not text.strip():
        return {
            "page": 0,
            "character": 0,
            "word": 0,
            "line": 0,
            "paragraph": 0,
            "uncertain_regions": [],
        }

    # Character-level: check Unicode validity
    char_confidences = []
    for i, char in enumerate(text):
        if "\u0900" <= char <= "\u097F":
            # Devanagari character — check sequence validity
            prev = text[i-1] if i > 0 else ""
            from app.extract.glyph_model import is_valid_devanagari_sequence
            if is_valid_devanagari_sequence(prev, char):
                char_confidences.append(90.0)
            else:
                char_confidences.append(40.0)
        elif char.isascii() and char.isalpha():
            char_confidences.append(85.0)
        elif char.isspace() or char in "।,.;:!?()-":
            char_confidences.append(95.0)
        else:
            char_confidences.append(70.0)

    char_conf = sum(char_confidences) / max(len(char_confidences), 1)

    # Word-level: check against knowledge base
    try:
        from app.intelligence.nepal_knowledge_base import is_known_word
        words = re.findall(r"[\u0900-\u097F]+", text)
        known = sum(1 for w in words if is_known_word(w))
        word_conf = (known / max(len(words), 1)) * 100 if words else 80.0
    except ImportError:
        word_conf = 70.0

    # Line-level
    lines = text.split("\n")
    line_confs = []
    for line in lines:
        if not line.strip():
            continue
        line_chars = [c for c in line if c.strip()]
        if not line_chars:
            continue
        deva = sum(1 for c in line_chars if "\u0900" <= c <= "\u097F")
        ratio = deva / len(line_chars) if line_chars else 0
        # Lines with very low or very high Devanagari are more confident
        if ratio > 0.7 or ratio < 0.1:
            line_confs.append(85.0)
        else:
            line_confs.append(60.0)  # Mixed content — less certain

    line_conf = sum(line_confs) / max(len(line_confs), 1) if line_confs else 70.0

    # Paragraph-level
    paragraphs = text.split("\n\n")
    para_confs = []
    for para in paragraphs:
        if len(para.strip()) < 10:
            continue
        para_confs.append(line_conf)  # Simplified — real version would be more nuanced

    para_conf = sum(para_confs) / max(len(para_confs), 1) if para_confs else 70.0

    # Page-level
    page_conf = (char_conf * 0.2 + word_conf * 0.35 + line_conf * 0.25 + para_conf * 0.2)

    # Identify uncertain regions (low-confidence zones)
    uncertain_regions = []
    # Find words with low confidence
    for word in re.findall(r"[\u0900-\u097F]+", text):
        try:
            from app.intelligence.nepal_knowledge_base import is_known_word, find_closest_word
            if not is_known_word(word) and len(word) >= 3:
                matches = find_closest_word(word, max_distance=2)
                if not matches:
                    uncertain_regions.append({
                        "word": word,
                        "confidence": 40.0,
                        "reason": "unknown_word_no_near_matches",
                    })
        except ImportError:
            pass

    return {
        "page": round(page_conf, 1),
        "character": round(char_conf, 1),
        "word": round(word_conf, 1),
        "line": round(line_conf, 1),
        "paragraph": round(para_conf, 1),
        "uncertain_regions": uncertain_regions[:20],  # Top 20 uncertain words
    }
