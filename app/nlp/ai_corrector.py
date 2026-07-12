"""
Font-aware PDF → Unicode text pipeline.

Primary path (default, highest accuracy):
  1. Font detection — identify Preeti/Kantipur/Sagarmatha/Unicode per span
  2. Direct extraction — read PDF text layer (no OCR)
  3. Mechanical conversion — npttf2utf + rule-based Unicode cleanup
  4. Rule-based Devanagari repair for legal/government text patterns

Optional AI refinement (off by default):
  Only runs when explicitly requested AND Ollama is available AND the
  mechanical output quality is low. AI output is accepted only if it scores
  higher than the mechanical conversion — otherwise mechanical text is kept.
"""
from __future__ import annotations

import os
import re
from collections import Counter
from typing import Any

from langchain_ollama import ChatOllama

from app.extract.direct_extract import extract_document_high_accuracy, score_text_quality
from app.nlp.nepali_sentence_intelligence import (
    corruption_score,
    repair_corrupted_devanagari,
    _DOCUMENT_TYPE_TO_KB_DOMAIN,
)
from app.logging_config import get_logger

logger = get_logger("AICorrector")

_DEFAULT_MODEL = "llama3"
_CHUNK_SIZE = 1200
_DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]+")

# Mechanical quality below this → optional AI may be attempted
_AI_QUALITY_THRESHOLD = 55.0

_ACTOR_SYSTEM = """\
You are an expert in Nepali Unicode text repair. The input was mechanically
converted from a legacy Nepali font (Preeti, Kantipur, etc.) to Unicode.

Fix ONLY obvious character errors (wrong matras, broken conjuncts, split words).
Do NOT add, remove, summarise, or repeat content.
Keep paragraph structure exactly as in the input.
Output ONLY the corrected text — no commentary."""

_ACTOR_USER = """\
DOCUMENT TYPE: {context}

TEXT TO FIX:
---
{chunk}
---

Output ONLY the corrected text:"""


def _devanagari_words(text: str) -> list[str]:
    return _DEVANAGARI_RE.findall(text)


def _text_quality_metrics(text: str) -> dict[str, float]:
    words = _devanagari_words(text)
    if len(words) < 3:
        return {
            "unique_ratio": 1.0,
            "consec_dup_ratio": 0.0,
            "dominant_word_ratio": 0.0,
            "word_count": len(words),
        }

    counts = Counter(words)
    consec_dup = sum(1 for i in range(1, len(words)) if words[i] == words[i - 1])

    return {
        "unique_ratio": len(counts) / len(words),
        "consec_dup_ratio": consec_dup / len(words),
        "dominant_word_ratio": counts.most_common(1)[0][1] / len(words),
        "word_count": len(words),
    }


def _is_garbled_repetition(text: str) -> bool:
    metrics = _text_quality_metrics(text)
    if metrics["word_count"] < 5:
        return False
    if metrics["consec_dup_ratio"] > 0.12:
        return True
    if metrics["dominant_word_ratio"] > 0.22:
        return True
    if metrics["unique_ratio"] < 0.35:
        return True
    return bool(
        re.search(r"([\u0900-\u097F]{1,12})\s+\1(?:\s+\1){2,}", text)
    )


def _is_llm_output_acceptable(candidate: str, original: str) -> bool:
    if not candidate or not candidate.strip():
        return False
    if _is_garbled_repetition(candidate):
        return False
    if len(original) > 80 and len(candidate) > len(original) * 1.35:
        return False

    cand = _text_quality_metrics(candidate)
    orig = _text_quality_metrics(original)
    if cand["consec_dup_ratio"] > orig["consec_dup_ratio"] + 0.05:
        return False
    if cand["dominant_word_ratio"] > orig["dominant_word_ratio"] + 0.08:
        return False
    if cand["unique_ratio"] + 0.08 < orig["unique_ratio"]:
        return False
    return True


def _ollama_available(base_url: str = "http://localhost:11434") -> bool:
    try:
        import urllib.request

        with urllib.request.urlopen(f"{base_url}/api/tags", timeout=3) as resp:
            return resp.status == 200
    except Exception:
        return False


def _detect_document_type(text: str) -> str:
    patterns = [
        (r"(ऐन|नियमावली|नियमहरू|विनियमावली)", "Nepali law / government act"),
        (r"(कार्यविधि|निर्देशिका|मार्गदर्शन)", "Nepali government procedure"),
        (r"(पाठ्यक्रम|पाठ्यपुस्तक|अध्याय)", "Nepali educational content"),
    ]
    for pattern, label in patterns:
        if re.search(pattern, text[:500], re.IGNORECASE):
            return label
    return "general Nepali document"


def _apply_rule_repairs(text: str) -> str:
    """Only repair when real OCR corruption markers are present."""
    if not text:
        return text
    if corruption_score(text) > 0:
        doc_type = _detect_document_type(text)
        domain = _DOCUMENT_TYPE_TO_KB_DOMAIN.get(doc_type)
        return repair_corrupted_devanagari(text, domain=domain)
    return text


def _actor_correct_chunk(
    chunk: str,
    llm: ChatOllama,
    doc_context: str,
) -> str:
    prompt = _ACTOR_USER.format(context=doc_context, chunk=chunk)
    try:
        resp = llm.invoke([
            {"role": "system", "content": _ACTOR_SYSTEM},
            {"role": "user", "content": prompt},
        ])
        corrected = resp.content.strip() if hasattr(resp, "content") else str(resp).strip()
        if _is_llm_output_acceptable(corrected, chunk):
            return corrected
        logger.warning("AI chunk rejected — keeping mechanical conversion for this section.")
        return chunk
    except Exception as exc:
        logger.warning("AI correction failed: %s", exc)
        return chunk


def _maybe_apply_ai(
    mechanical_text: str,
    model_name: str,
    base_url: str,
) -> tuple[str, bool, str | None]:
    """
    Optionally refine mechanical text with AI.
    Returns (final_text, ai_applied, skip_reason).
    """
    mech_score = score_text_quality(mechanical_text)["score"]

    if mech_score >= _AI_QUALITY_THRESHOLD:
        logger.info(
            "Mechanical conversion quality %.1f ≥ threshold — skipping AI.", mech_score
        )
        return mechanical_text, False, "mechanical_quality_sufficient"

    if not _ollama_available(base_url):
        return mechanical_text, False, "ollama_unavailable"

    logger.info(
        "Mechanical quality %.1f < threshold — attempting optional AI refinement.",
        mech_score,
    )

    llm = ChatOllama(
        model=model_name,
        base_url=base_url,
        temperature=0.05,
        num_predict=1200,
        repeat_penalty=1.2,
        top_p=0.9,
    )

    doc_context = _detect_document_type(mechanical_text)
    paragraphs = mechanical_text.split("\n\n")
    chunks: list[str] = []
    current = ""
    for para in paragraphs:
        if len(current) + len(para) + 2 > _CHUNK_SIZE and current:
            chunks.append(current.strip())
            current = para
        else:
            current += ("\n\n" if current else "") + para
    if current.strip():
        chunks.append(current.strip())

    ai_chunks = [_actor_correct_chunk(c, llm, doc_context) for c in chunks]
    ai_text = "\n\n".join(ai_chunks)

    if _is_garbled_repetition(ai_text):
        logger.warning("AI output garbled — keeping mechanical conversion.")
        return mechanical_text, False, "ai_output_rejected"

    ai_score = score_text_quality(ai_text)["score"]
    if ai_score > mech_score + 2.0:
        logger.info("AI improved quality %.1f → %.1f", mech_score, ai_score)
        return ai_text, True, None

    logger.info(
        "AI did not improve quality (%.1f vs %.1f) — keeping mechanical.",
        ai_score, mech_score,
    )
    return mechanical_text, False, "ai_no_improvement"


def process_pdf_smart(
    pdf_bytes: bytes,
    model_name: str = _DEFAULT_MODEL,
    *,
    use_ai: bool = False,
) -> dict[str, Any]:
    """
    High-accuracy PDF → Unicode pipeline.

    Default: mechanical font conversion only (more accurate than OCR for
    PDFs with embedded text layers).

    Optional AI: only when use_ai=True and ENABLE_LLM_OCR_ENHANCEMENT is set.
    """
    logger.info("Step 1/2 — Direct font-aware extraction (no OCR) …")
    result = extract_document_high_accuracy(pdf_bytes)

    mechanical_text = _apply_rule_repairs(result["text"])
    font_analysis = result["font_analysis"]
    quality = score_text_quality(mechanical_text)

    if not mechanical_text.strip() or mechanical_text.startswith("[No text layer"):
        return {
            "text": mechanical_text,
            "font_analysis": font_analysis,
            "pages": result["pages"],
            "raw_converted": "",
            "ai_applied": False,
            "iterations": 0,
            "confidence": 0.0,
            "quality": quality,
            "method": result["method"],
            "ai_skipped_reason": "no_text_layer",
        }

    ai_enabled = (
        use_ai
        and os.getenv("ENABLE_LLM_OCR_ENHANCEMENT", "false").lower() in ("1", "true", "yes")
    )

    if not ai_enabled:
        logger.info(
            "Step 2/2 — Mechanical conversion complete (score %.1f, confidence %.1f%%).",
            quality["score"], result["confidence"],
        )
        return {
            "text": mechanical_text,
            "font_analysis": font_analysis,
            "pages": result["pages"],
            "raw_converted": mechanical_text,
            "ai_applied": False,
            "iterations": 0,
            "confidence": result["confidence"],
            "quality": quality,
            "method": result["method"],
            "ai_skipped_reason": "ai_disabled",
        }

    ollama_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    final_text, ai_applied, skip_reason = _maybe_apply_ai(
        mechanical_text, model_name, ollama_url
    )

    return {
        "text": final_text,
        "font_analysis": font_analysis,
        "pages": result["pages"],
        "raw_converted": mechanical_text,
        "ai_applied": ai_applied,
        "iterations": 1 if ai_applied else 0,
        "confidence": result["confidence"],
        "quality": score_text_quality(final_text),
        "method": "direct_font_conversion+ai" if ai_applied else result["method"],
        "ai_skipped_reason": skip_reason,
    }
