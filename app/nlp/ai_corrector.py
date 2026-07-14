"""
Font-aware PDF -> Unicode text pipeline.

  1. Font detection — identify Preeti/Kantipur/Sagarmatha/Unicode per span
  2. Direct extraction — read PDF text layer (no OCR)
  3. Mechanical conversion — npttf2utf + rule-based Unicode cleanup
  4. Rule-based Devanagari repair for legal/government text patterns

This module previously also had an optional Ollama/langchain-based AI
refinement step. It was removed: it required a local LLM (Ollama +
Mistral/Llama3) that was never actually downloaded in the deployment
image, could not realistically run in the memory budget of the hosting
tier this app is deployed on, and contradicted this project's own README
("no AI/LLM/cloud recognition components"). See git history for the
removed implementation if this is revisited with a properly-resourced
Ollama deployment.
"""
from __future__ import annotations

import re
from typing import Any

from app.extract.direct_extract import extract_document_high_accuracy, score_text_quality
from app.nlp.nepali_sentence_intelligence import (
    corruption_score,
    repair_corrupted_devanagari,
    _DOCUMENT_TYPE_TO_KB_DOMAIN,
)
from app.logging_config import get_logger

logger = get_logger("AICorrector")


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


def process_pdf_smart(pdf_bytes: bytes) -> dict[str, Any]:
    """
    High-accuracy PDF -> Unicode pipeline.

    Reads the PDF text layer directly and converts legacy Nepali fonts
    (Preeti, Kantipur, Sagarmatha, etc.) to proper Unicode. This is more
    accurate than OCR for PDFs that already contain embedded text.
    """
    logger.info("Direct font-aware extraction (no OCR) …")
    result = extract_document_high_accuracy(pdf_bytes)

    mechanical_text = _apply_rule_repairs(result["text"])
    font_analysis = result["font_analysis"]
    quality = score_text_quality(mechanical_text)

    if not mechanical_text.strip() or mechanical_text.startswith("[No text layer"):
        return {
            "text": mechanical_text,
            "font_analysis": font_analysis,
            "pages": result["pages"],
            "quality": quality,
            "method": result["method"],
            "tables_detected": result.get("tables_detected", 0),
            "tables_by_method": result.get("tables_by_method", {}),
        }

    logger.info(
        "Mechanical conversion complete (score %.1f, confidence %.1f%%).",
        quality["score"], result["confidence"],
    )
    return {
        "text": mechanical_text,
        "font_analysis": font_analysis,
        "pages": result["pages"],
        "confidence": result["confidence"],
        "quality": quality,
        "method": result["method"],
        "tables_detected": result.get("tables_detected", 0),
        "tables_by_method": result.get("tables_by_method", {}),
    }
