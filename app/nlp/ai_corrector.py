"""
Font-aware AI language understanding and Unicode conversion pipeline.

Pipeline per document:
  1. Font Detection    — identify every font (Preeti/Kantipur/Sagarmatha/etc.)
  2. Raw Conversion    — convert each text span using the correct npttf2utf map
  3. Actor Pass        — LLM corrects the full text with deep domain context
  4. Critic Pass       — LLM reviews sentence-by-sentence, flags broken ones
  5. Targeted Fix Pass — LLM re-corrects ONLY flagged sentences with extra hints
  6. Repeat 4-5        — up to MAX_ITERATIONS until all sentences pass review
  7. Output            — semantically verified, clean Unicode .txt

The Critic-Actor loop ensures every sentence makes contextual sense before
the text is accepted as final output.

Supports: Preeti, Kantipur, e-Kantipur, Sagarmatha, Himali, Aakriti,
          PCS Nepali, Siddhi, Kanchan, Navjeevan, Fontasy Himali,
          Vishwash, Ganesh/Ganess, and any standard Unicode Devanagari font.
"""
from __future__ import annotations

import json
import re
from typing import Any

import fitz
from langchain_ollama import ChatOllama

from app.legacy_fonts.converter import convert_legacy_text
from app.legacy_fonts.mappings import is_legacy_font
from app.nlp.font_detector import analyse_document_fonts
from app.nlp.nepali_sentence_intelligence import (
    analyze_sentence_meaning,
    repair_corrupted_devanagari,
    synthesize_sentence_context,
)
from app.logging_config import get_logger

logger = get_logger("AICorrector")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_DEFAULT_MODEL   = "llama3"
_CHUNK_SIZE      = 1500   # characters per LLM call (safe for 4k context)
_MAX_ITERATIONS  = 3      # max Critic-Actor rounds per chunk

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

# ── Actor prompt ─────────────────────────────────────────────────────────────
_ACTOR_SYSTEM = """\
You are an expert in Nepali and English language, Nepali law, government documents,
and Devanagari Unicode text. You specialise in recovering text that was mechanically
converted from legacy Nepali fonts (Preeti, Kantipur, Sagarmatha, Himali, Aakriti,
PCS Nepali, etc.) to Unicode.

The input text has ALREADY been mechanically converted but may still have:
  • Wrong Devanagari characters that look visually similar but are incorrect
    e.g. "ढ्ो" instead of "ने", "ठ" instead of "त", garbled conjuncts
  • Split or merged words caused by bad font-span boundaries
  • Wrong matras (vowel signs) attached to wrong consonants
  • Missing or extra anusvara (ं), chandrabindu (ँ), visarga (ः), halant (्)
  • Numbers / dates that are correct — do NOT change them
  • Mixed Nepali-English content — preserve both

YOUR TASK:
  1. Read each sentence and FULLY UNDERSTAND its MEANING in context.
  2. Use NEPALI SENTENCE CONTEXT (clause roles, legal domain, repaired OCR hints)
     when provided — infer meaning from sentence structure, not corrupted glyphs.
  3. Fix every character that breaks the meaning — not just obvious typos.
  4. Common patterns to watch and fix:
       "ढ्ो"  →  "ने"       "ठ"    →  "त" (context-dependent)
       "प्ाठ" →  "पाठ"     "ड"    →  "ड" or "ड" check context
       "ि" before consonant instead of after → reorder
       Legal docs: "नियमवाली", "राजपत्र", "मिति", "संक्षिप्त नाम", "दफा"
  5. Output ONLY the corrected Nepali/English text.
  6. Keep paragraph and line structure exactly as in the input.
  7. Do NOT translate, summarise, or add any commentary.
  8. Do NOT add markdown, bullets, or headings not present in input.
"""

_ACTOR_USER = """\
DOCUMENT CONTEXT: {context}

SENTENCE MEANING CONTEXT:
{sentence_context}

FONT USED: {font_family} (legacy encoding mechanically converted to Unicode)

RAW CONVERTED TEXT (correct this):
---
{chunk}
---

Output ONLY the corrected text. Nothing else."""


# ── Critic prompt ────────────────────────────────────────────────────────────
_CRITIC_SYSTEM = """\
You are an expert Nepali language reviewer and proofreader. Your job is to
review corrected Nepali text and identify sentences that STILL do not make
semantic sense — sentences where the meaning is unclear, garbled, or where
characters are obviously wrong.

Respond in JSON only. No other text.
Format:
{
  "all_correct": true/false,
  "issues": [
    {
      "sentence": "<exact sentence that is still broken>",
      "problem": "<short description of what is wrong>",
      "suggested_fix": "<your best guess at what it should say>"
    }
  ]
}

If all sentences are correct and meaningful, respond:
{"all_correct": true, "issues": []}
"""

_CRITIC_USER = """\
Review the following corrected text. Identify any sentence that still has
wrong characters, broken meaning, or is not proper Nepali/English.

SENTENCE MEANING CONTEXT (what each clause should mean):
{sentence_context}

TEXT TO REVIEW:
---
{text}
---

Respond in JSON only."""


# ── Targeted fix prompt ──────────────────────────────────────────────────────
_FIXER_SYSTEM = """\
You are an expert Nepali Unicode text repair specialist. You will be given:
  1. A broken sentence that doesn't make sense
  2. A description of what's wrong
  3. A suggested correct version
  4. The surrounding context

Using all of this, output ONLY the single corrected sentence. Nothing else.
Preserve the original meaning and language (Nepali/English/mixed)."""

_FIXER_USER = """\
BROKEN SENTENCE: {sentence}
PROBLEM: {problem}
SUGGESTED FIX: {suggested}
SURROUNDING CONTEXT:
{context}

Output ONLY the corrected single sentence:"""


# ---------------------------------------------------------------------------
# Document type detector (for Actor context)
# ---------------------------------------------------------------------------

_DOC_PATTERNS = [
    (r"(ऐन|नियमावली|नियमहरू|विनियमावली)", "Nepali law / government act"),
    (r"(कार्यविधि|निर्देशिका|मार्गदर्शन)", "Nepali government procedure / guideline"),
    (r"(सम्झौता|करार|अनुबन्ध)", "Nepali legal agreement / contract"),
    (r"(निर्णय|आदेश|फैसला)", "Nepali court order / decision"),
    (r"(पाठ्यक्रम|पाठ्यपुस्तक|अध्याय)", "Nepali educational content"),
    (r"(समाचार|रिपोर्ट|प्रतिवेदन)", "Nepali news article / report"),
    (r"(invoice|bill|receipt|amount|total)", "financial document"),
    (r"(वार्षिक|बजेट|लेखा|हिसाब)", "Nepali financial / budget document"),
]


def _detect_document_type(text: str) -> str:
    for pattern, label in _DOC_PATTERNS:
        if re.search(pattern, text[:500], re.IGNORECASE):
            return label
    return "general Nepali document"


# ---------------------------------------------------------------------------
# Span-level raw conversion
# ---------------------------------------------------------------------------

def _raw_convert_span(text: str, font_name: str) -> str:
    """Convert a single text span using the correct font map."""
    if not text or not text.strip():
        return text
    if is_legacy_font(font_name):
        return convert_legacy_text(text, font_name)
    return text


# ---------------------------------------------------------------------------
# Page-level extraction with per-span font-aware conversion
# ---------------------------------------------------------------------------

def _extract_page_font_aware(page: fitz.Page) -> str:
    """
    Extract text from one PDF page.
    Each span is converted using the font detected for that span.
    """
    page_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
    blocks_out: list[str] = []

    for block in page_dict.get("blocks", []):
        if block.get("type") != 0:
            continue
        block_lines: list[str] = []
        for line in block.get("lines", []):
            line_parts: list[tuple[str, float]] = []
            for span in line.get("spans", []):
                font_name = span.get("font", "")
                raw_text  = span.get("text", "")
                bbox      = span.get("bbox", (0, 0, 0, 0))
                x0        = bbox[0]
                if not raw_text.strip():
                    continue
                converted = _raw_convert_span(raw_text, font_name)
                line_parts.append((converted, x0))

            if not line_parts:
                continue

            line_parts.sort(key=lambda t: t[1])
            line_text = ""
            for i, (text, x0) in enumerate(line_parts):
                if i == 0:
                    line_text = text
                else:
                    gap = x0 - line_parts[i - 1][1]
                    sep = "\t" if gap > 50 else (" " if gap > 5 else "")
                    line_text += sep + text

            block_lines.append(line_text)

        if block_lines:
            blocks_out.append("\n".join(block_lines))

    return "\n\n".join(blocks_out)


# ---------------------------------------------------------------------------
# Post-conversion cleanup (before AI sees it)
# ---------------------------------------------------------------------------

_CLEANUP_RULES = [
    (r"ाा+", "ा"), (r"िि+", "ि"), (r"ीी+", "ी"),
    (r"ुु+", "ु"), (r"ूू+", "ू"), (r"ेे+", "े"),
    (r"ैै+", "ै"), (r"ोो+", "ो"), (r"ौौ+", "ौ"),
    (r"्+", "्"), (r"्\s", " "), (r"।।", "।"),
    (r"[ \t]+", " "), (r"\n{4,}", "\n\n\n"),
]


def _cleanup(text: str) -> str:
    for pattern, replacement in _CLEANUP_RULES:
        text = re.sub(pattern, replacement, text)
    return text.strip()


# ---------------------------------------------------------------------------
# Critic-Actor correction engine
# ---------------------------------------------------------------------------

def _actor_correct(
    chunk: str,
    llm: ChatOllama,
    doc_context: str,
    font_family: str,
    sentence_context: str = "",
) -> str:
    """Run the Actor: deeply correct one chunk of converted text."""
    prompt = _ACTOR_USER.format(
        context=doc_context,
        sentence_context=sentence_context or "(none)",
        font_family=font_family,
        chunk=chunk,
    )
    try:
        resp = llm.invoke([
            {"role": "system", "content": _ACTOR_SYSTEM},
            {"role": "user",   "content": prompt},
        ])
        return resp.content.strip() if hasattr(resp, "content") else str(resp).strip()
    except Exception as exc:
        logger.warning("Actor pass failed: %s", exc)
        return chunk


def _critic_review(text: str, llm: ChatOllama, sentence_context: str = "") -> list[dict]:
    """
    Run the Critic: review corrected text, return list of issue dicts.
    Each issue: {sentence, problem, suggested_fix}
    Returns [] if everything is correct.
    """
    prompt = _CRITIC_USER.format(
        text=text,
        sentence_context=sentence_context or "(none)",
    )
    try:
        resp = llm.invoke([
            {"role": "system", "content": _CRITIC_SYSTEM},
            {"role": "user",   "content": prompt},
        ])
        raw = resp.content.strip() if hasattr(resp, "content") else str(resp).strip()
        # Extract JSON (handle markdown fences)
        json_match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not json_match:
            return []
        parsed = json.loads(json_match.group())
        if parsed.get("all_correct"):
            return []
        return parsed.get("issues", [])
    except Exception as exc:
        logger.warning("Critic pass failed (JSON parse or LLM): %s", exc)
        return []


def _fixer_fix_sentence(
    issue: dict,
    surrounding_context: str,
    llm: ChatOllama,
) -> str:
    """
    Run the targeted Fixer on one broken sentence identified by the Critic.
    Returns the fixed sentence.
    """
    prompt = _FIXER_USER.format(
        sentence=issue["sentence"],
        problem=issue.get("problem", "unknown"),
        suggested=issue.get("suggested_fix", "unknown"),
        context=surrounding_context[:400],
    )
    try:
        resp = llm.invoke([
            {"role": "system", "content": _FIXER_SYSTEM},
            {"role": "user",   "content": prompt},
        ])
        fixed = resp.content.strip() if hasattr(resp, "content") else str(resp).strip()
        return fixed or issue["sentence"]
    except Exception as exc:
        logger.warning("Fixer pass failed: %s", exc)
        return issue.get("suggested_fix") or issue["sentence"]


def _apply_fixes(text: str, issues: list[dict], llm: ChatOllama) -> str:
    """Apply targeted sentence-level fixes identified by the Critic."""
    result = text
    for issue in issues:
        broken = issue.get("sentence", "").strip()
        if not broken or broken not in result:
            continue
        # Get 3 lines of surrounding context
        idx = result.find(broken)
        ctx_start = max(0, idx - 200)
        ctx_end   = min(len(result), idx + len(broken) + 200)
        ctx = result[ctx_start:ctx_end]

        fixed = _fixer_fix_sentence(issue, ctx, llm)
        if fixed and fixed != broken:
            result = result.replace(broken, fixed, 1)
            logger.info("Fixed: '%s' → '%s'", broken[:60], fixed[:60])

    return result


def _critic_actor_loop(
    chunk: str,
    llm: ChatOllama,
    doc_context: str,
    font_family: str,
    max_iter: int = _MAX_ITERATIONS,
) -> tuple[str, int]:
    """
    Full Critic-Actor loop for one chunk.

    Returns (final_text, iterations_run).
    """
    sentence_context = synthesize_sentence_context(chunk)

    # ── Actor: initial correction ───────────────────────────────────────────
    current = _actor_correct(chunk, llm, doc_context, font_family, sentence_context)

    for iteration in range(1, max_iter + 1):
        # ── Critic: review ──────────────────────────────────────────────────
        issues = _critic_review(current, llm, sentence_context)

        if not issues:
            logger.info("Critic satisfied after %d iteration(s).", iteration)
            return current, iteration

        logger.info(
            "Critic found %d issue(s) in iteration %d — running targeted fixes.",
            len(issues), iteration,
        )

        # ── Targeted fixer: fix flagged sentences ───────────────────────────
        current = _apply_fixes(current, issues, llm)

    logger.info("Max iterations (%d) reached — accepting current text.", max_iter)
    return current, max_iter


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def process_pdf_smart(pdf_bytes: bytes, model_name: str = _DEFAULT_MODEL) -> dict[str, Any]:
    """
    Full font-aware Critic-Actor AI pipeline on a PDF document.

    Pipeline:
      1. Detect fonts in the PDF
      2. Extract + mechanically convert each text span
      3. Chunk the converted text
      4. For each chunk: Actor corrects → Critic reviews → Fixer fixes →
         repeat until Critic is satisfied (or max_iter reached)
      5. Reassemble and return verified clean Unicode text

    Returns:
        {
            "text":          str   — final clean Unicode text
            "font_analysis": dict  — from analyse_document_fonts()
            "pages":         int
            "raw_converted": str   — text after mechanical conversion (pre-AI)
            "ai_applied":    bool
            "iterations":    int   — total Critic-Actor iterations run
        }
    """
    # ── Step 1: Font detection ──────────────────────────────────────────────
    logger.info("Step 1/4 — Analysing fonts in PDF …")
    font_analysis = analyse_document_fonts(pdf_bytes)
    font_family   = font_analysis.get("dominant_family", "unknown")
    logger.info("Font analysis: %s", font_analysis["summary"])

    # ── Step 2: Span-level raw conversion ──────────────────────────────────
    logger.info("Step 2/4 — Extracting and converting text spans …")
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        raise ValueError(f"Cannot open PDF: {exc}") from exc

    page_texts: list[str] = []
    try:
        for i in range(len(doc)):
            page = doc.load_page(i)
            page_texts.append(_extract_page_font_aware(page))
    finally:
        doc.close()

    raw_unicode = "\n\n".join(pt for pt in page_texts if pt.strip())
    raw_unicode = _cleanup(raw_unicode)
    if re.search(r"[\u0900-\u097F]", raw_unicode):
        raw_unicode = repair_corrupted_devanagari(raw_unicode)

    if not raw_unicode.strip():
        return {
            "text": "[No text layer found in PDF.]",
            "font_analysis": font_analysis,
            "pages": len(page_texts),
            "raw_converted": "",
            "ai_applied": False,
            "iterations": 0,
        }

    # ── Step 3: Document context detection ─────────────────────────────────
    doc_context = _detect_document_type(raw_unicode)
    meaning = analyze_sentence_meaning(raw_unicode[:2000])
    if meaning.summary_english:
        doc_context = f"{doc_context}; {meaning.summary_english}"
    logger.info("Document context detected: %s", doc_context)

    # ── Step 4: Critic-Actor correction loop ────────────────────────────────
    logger.info("Step 3/4 — Running Critic-Actor AI correction loop …")

    # Use lower temperature for the Actor (deterministic corrections)
    # and slightly higher for the Critic (to catch subtle issues)
    llm = ChatOllama(
        model=model_name,
        temperature=0.05,
        num_predict=4000,
    )

    # Split into chunks (split at paragraph boundaries where possible)
    paragraphs = raw_unicode.split("\n\n")
    chunks: list[str] = []
    current_chunk = ""
    for para in paragraphs:
        if len(current_chunk) + len(para) + 2 > _CHUNK_SIZE and current_chunk:
            chunks.append(current_chunk.strip())
            current_chunk = para
        else:
            current_chunk += ("\n\n" if current_chunk else "") + para
    if current_chunk.strip():
        chunks.append(current_chunk.strip())

    corrected_chunks: list[str] = []
    total_iterations = 0

    for idx, chunk in enumerate(chunks):
        logger.info(
            "Processing chunk %d/%d (%d chars) …", idx + 1, len(chunks), len(chunk)
        )
        corrected, iters = _critic_actor_loop(
            chunk, llm, doc_context, font_family
        )
        corrected_chunks.append(corrected)
        total_iterations += iters

    final_text = "\n\n".join(corrected_chunks)
    ai_applied = final_text.strip() != raw_unicode.strip()

    logger.info(
        "Pipeline complete. Pages=%d, FontStrategy=%s, TotalIterations=%d, AIApplied=%s",
        len(page_texts), font_analysis["strategy"], total_iterations, ai_applied,
    )

    return {
        "text": final_text,
        "font_analysis": font_analysis,
        "pages": len(page_texts),
        "raw_converted": raw_unicode,
        "ai_applied": ai_applied,
        "iterations": total_iterations,
    }
