"""
Nepali sentence intelligence — OCR corruption repair, clause context, meaning synthesis.

Used before and during AI correction so the Actor/Critic understand sentence
meaning and legal context, not just individual corrupted glyphs.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Literal

CORRUPTION_RE = re.compile(
    r"[\uFFFD\uFFFE\uFFFF\u25A1\u25A0\u25AB\u25AA\u25FB\u25FC\u2610\u2611\u2612"
    r"□▯▢■◻◼⬜⬛\uE000-\uF8FF]"
)
DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]")

# Legal, government, and common document vocabulary
DOMAIN_LEXICON = [
    "नियमवाली", "नियमावली", "ऐन", "दफा", "नियम", "संशोधन", "प्रारम्भ", "संक्षिप्त", "नाम",
    "राजपत्र", "मिति", "प्रकाशित", "सरकारी", "गजेट", "अधिकार", "प्रावधान", "परिभाषा",
    "आयकर", "कर", "उद्योग", "पेट्रोलियम", "विनियम", "कार्यविधि", "निर्देशिका",
    "सम्झौता", "करार", "निर्णय", "आदेश", "फैसला", "अनुसूची", "परिच्छेद",
]

CONTEXT_REPAIRS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(
            r"(पेट्रोलियम\s+उद्योग\s*\(\s*आयकर\s*\))\s*[^\u0900-\u097F\u0964।,]{0,12}"
            r"[\u0900-\u097F]{0,12}\s*[,，]\s*(२०\d{2})"
        ),
        r"\1 नियमवाली, \2",
    ),
    (
        re.compile(
            r"(नेपाल)\s+(?:[^\u0900-\u097F\u0964।]*[\u0900-\u097F]{0,4})?पत्र(मा\s+प्रकाशित)"
        ),
        r"\1 राजपत्र\2",
    ),
    (
        re.compile(
            r"(प्रकाशित)\s+(?:[^\u0900-\u097F\u0964।]*[\u0900-\u097F]{0,4})?ति\s*[:：]"
        ),
        r"\1 मिति :",
    ),
    (
        re.compile(
            r"(संक्षिप्त)\s+(?:[^\u0900-\u097F\u0964।]*[\u0900-\u097F]{0,4})?(?:र\s+प्रारम्भ)"
        ),
        r"\1 नाम र प्रारम्भ",
    ),
    (
        re.compile(r"(आयकर\s+ऐन[^।]*दफा\s+\d+)"),
        r"\1",
    ),
]

DomainHint = Literal["legal", "general"]


def _consonant_skeleton(word: str) -> str:
    word = re.sub(r"[\u093E-\u094F\u0962\u0963\u094D\u0903\u0902]", "", word)
    return re.sub(r"[^\u0900-\u097F]", "", word)


def _levenshtein(a: str, b: str) -> int:
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i]
        for j, cb in enumerate(b, 1):
            curr.append(min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = curr
    return prev[-1]


def corruption_score(text: str) -> float:
    matches = CORRUPTION_RE.findall(text)
    if not matches:
        return 0.0
    return min(1.0, len(matches) / max(len(text) / 20, 1))


def repair_corrupted_devanagari(text: str) -> str:
    """Repair OCR/encoding corruption using context templates and domain lexicon.

    IMPORTANT SCOPING RULE: the domain-lexicon distance-match below is only
    ever applied to a word that itself contained a genuine corruption
    marker (private-use-area char, replacement char, or a geometric-shape
    OCR-garbage placeholder such as [square]/[filled square]) -- never to
    ordinary words elsewhere in the same text block.

    A previous version of this function ran the lexicon distance-match
    against *every* Devanagari word >= 3 characters in the entire text,
    as soon as `corruption_score(text) > 0` anywhere in that text (the
    caller-side gate in ai_corrector.py / nepali_postprocess.py). Since
    `corruption_score` can be a tiny positive number from a single stray
    corruption character in an otherwise-clean multi-thousand-character
    document, this meant one stray OCR artifact anywhere in the page
    triggered force-replacement of hundreds of unrelated, perfectly
    correct words (proper nouns, place names, English loanword
    transliterations, technical terms) with whichever of the ~28
    DOMAIN_LEXICON entries happened to be closest by consonant-skeleton
    edit distance -- producing exactly the kind of "नियम"/"ऐन"/"दफा"/
    "मिति"/"फैसला" noise flooding real documents. Only words that
    actually contain a corruption marker are now eligible for lexicon
    substitution; every other word passes through unchanged.
    """
    if not text or not text.strip():
        return ""
    out = unicodedata.normalize("NFKC", text.strip())
    for pattern, repl in CONTEXT_REPAIRS:
        out = pattern.sub(repl, out)

    parts = re.split(r"(\s+|[।.،,;:!?]+)", out)
    repaired: list[str] = []
    for part in parts:
        has_marker = bool(CORRUPTION_RE.search(part))
        # Strip corruption-marker characters from this part regardless of
        # whether we go on to lexicon-match it, so garbage glyphs never
        # survive into the output.
        cleaned = CORRUPTION_RE.sub("", part)

        if not has_marker:
            # This word/segment was never actually flagged as corrupted --
            # leave it completely untouched (this is the fix: previously
            # every word here was still lexicon-matched).
            repaired.append(part)
            continue

        if not DEVANAGARI_RE.search(cleaned) or len(cleaned.strip()) < 3:
            repaired.append(cleaned)
            continue
        if re.fullmatch(r"[२०-९।.\-/]+", cleaned.strip()):
            repaired.append(cleaned)
            continue

        skel = _consonant_skeleton(cleaned)
        best, best_dist = cleaned, 999
        for lex in DOMAIN_LEXICON:
            lex_skel = _consonant_skeleton(lex)
            if not lex_skel:
                continue
            dist = _levenshtein(skel, lex_skel)
            threshold = max(2, int(len(lex_skel) * 0.45))
            if dist <= threshold and dist < best_dist:
                best_dist = dist
                best = lex
        repaired.append(best if best_dist < 999 else cleaned)
    out = "".join(repaired)
    return re.sub(r"\s+", " ", out).strip()


def segment_clauses(text: str) -> list[str]:
    normalized = re.sub(r"\s+", " ", (text or "").strip())
    if not normalized:
        return []
    raw = re.split(
        r"(?<=[।.;])\s+|(?:\s+)(?:ra|र|tara|तर|bhane|भने|ki|कि|ani|अनि)\s+",
        normalized,
        flags=re.I,
    )
    return [c.strip() for c in raw if c.strip() and len(c.strip()) >= 3]


@dataclass
class ClauseAnalysis:
    text: str
    domain_hint: DomainHint = "general"
    is_question: bool = False


@dataclass
class SentenceMeaning:
    original_text: str
    repaired_text: str
    corruption_score: float
    clauses: list[ClauseAnalysis]
    summary_english: str


_LEGAL_MARKERS = re.compile(
    r"नियमवाली|नियमावली|ऐन|दफा|राजपत्र|संशोधन|प्रावधान|आयकर|गजेट",
)


def analyze_clause(clause: str) -> ClauseAnalysis:
    domain: DomainHint = "legal" if _LEGAL_MARKERS.search(clause) else "general"
    is_q = bool(re.search(r"के\s*हो|कति|कसरी|किन|\?", clause))
    return ClauseAnalysis(text=clause, domain_hint=domain, is_question=is_q)


def analyze_sentence_meaning(raw_text: str) -> SentenceMeaning:
    original = (raw_text or "").strip()
    has_dev = bool(DEVANAGARI_RE.search(original))
    needs_repair = has_dev and (
        bool(CORRUPTION_RE.search(original)) or corruption_score(original) > 0
    )
    repaired = repair_corrupted_devanagari(original) if needs_repair else original
    work = repaired or original
    clauses = [analyze_clause(c) for c in segment_clauses(work)]

    en_parts: list[str] = []
    if clauses and clauses[0].domain_hint == "legal":
        en_parts.append("Legal/regulatory Nepali document")
    if repaired != original:
        en_parts.append("OCR corruption repaired")

    return SentenceMeaning(
        original_text=original,
        repaired_text=work,
        corruption_score=corruption_score(original),
        clauses=clauses,
        summary_english=" · ".join(en_parts) or work[:120],
    )


def synthesize_sentence_context(message: str, max_chars: int = 1200) -> str:
    """Compact meaning context for LLM Actor/Critic prompts."""
    analysis = analyze_sentence_meaning(message)
    if not analysis.clauses and analysis.corruption_score == 0:
        return ""

    lines = [
        "NEPALI SENTENCE INTELLIGENCE (use for meaning, not literal glyph match):",
        f"Document sense: {analysis.summary_english}",
    ]
    if analysis.repaired_text != analysis.original_text:
        lines.append(f"Rule-based OCR repair preview: {analysis.repaired_text[:300]}")
    for clause in analysis.clauses[:5]:
        tag = clause.domain_hint
        lines.append(f"  • [{tag}] {clause.text[:100]}")
    out = "\n".join(lines).strip()
    return out if len(out) <= max_chars else out[: max_chars - 3] + "..."
