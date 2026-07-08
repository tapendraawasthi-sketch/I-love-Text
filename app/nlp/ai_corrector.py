"""
Font-aware AI language understanding and Unicode conversion pipeline.

Pipeline per document:
  1. Font Detection   — identify every font (Preeti/Kantipur/Sagarmatha/etc.)
  2. Raw Conversion   — convert each text span using the correct npttf2utf map
                        (or the built-in Preeti map as fallback)
  3. AI Correction    — LLM reads the converted text, understands the meaning,
                        fixes spelling/grammar, normalises Unicode
  4. Output           — clean, semantically-correct Unicode .txt

Supports: Preeti, Kantipur, e-Kantipur, Sagarmatha, Himali, Aakriti,
          PCS Nepali, Siddhi, Kanchan, Navjeevan, Fontasy Himali,
          Vishwash, Ganesh/Ganess, and any standard Unicode Devanagari font.
"""
from __future__ import annotations

import re
from typing import Any

import fitz
from langchain_ollama import ChatOllama

from app.legacy_fonts.converter import convert_legacy_text
from app.legacy_fonts.mappings import is_legacy_font
from app.nlp.font_detector import analyse_document_fonts, identify_font
from app.logging_config import get_logger

logger = get_logger("AICorrector")

# ---------------------------------------------------------------------------
# LLM configuration
# ---------------------------------------------------------------------------

_DEFAULT_MODEL = "llama3"

_SYSTEM_PROMPT = """\
You are an expert Nepali and English language understanding AI.
You will receive text that has been extracted and mechanically converted to \
Unicode from a PDF document. The text may contain:
  - Minor conversion artefacts (wrong matras, missing halants, split words)
  - Spelling mistakes or typos
  - Garbled characters where the font mapping was imperfect

Your task:
  1. Read the text and UNDERSTAND its meaning.
  2. Fix ALL spelling errors, wrong characters, and conversion artefacts.
  3. Output ONLY the corrected, clean, semantically-accurate Unicode text.
  4. Preserve line breaks, paragraph structure, and numbers.
  5. Do NOT add commentary, explanations, or markdown formatting.
  6. Do NOT translate — keep the original language (Nepali/English/mixed).
"""

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
    Returns the reconstructed plain text for the page.
    """
    page_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
    blocks_out: list[str] = []

    for block in page_dict.get("blocks", []):
        if block.get("type") != 0:
            continue
        block_lines: list[str] = []
        for line in block.get("lines", []):
            line_parts: list[tuple[str, float]] = []  # (converted_text, x0)
            for span in line.get("spans", []):
                font_name = span.get("font", "")
                raw_text = span.get("text", "")
                bbox = span.get("bbox", (0, 0, 0, 0))
                x0 = bbox[0]
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
# AI semantic correction
# ---------------------------------------------------------------------------

def _ai_correct(raw_unicode: str, model_name: str = _DEFAULT_MODEL) -> str:
    """
    Pass converted Unicode text through the LLM for semantic understanding
    and auto-correction.

    Falls back to the raw_unicode if LLM is unavailable.
    """
    if not raw_unicode.strip():
        return raw_unicode

    # Chunk into ~2000-char pieces to stay within context limits
    chunk_size = 2000
    chunks = [raw_unicode[i: i + chunk_size]
              for i in range(0, len(raw_unicode), chunk_size)]

    try:
        llm = ChatOllama(model=model_name, temperature=0.05, num_predict=3000)
        corrected_chunks: list[str] = []
        for chunk in chunks:
            messages = [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": chunk},
            ]
            resp = llm.invoke(messages)
            corrected_chunks.append(
                resp.content.strip() if hasattr(resp, "content") else str(resp).strip()
            )
        return "\n\n".join(corrected_chunks)
    except Exception as exc:
        logger.error("LLM correction failed: %s — returning pre-corrected text", exc)
        return raw_unicode


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def process_pdf_smart(pdf_bytes: bytes, model_name: str = _DEFAULT_MODEL) -> dict[str, Any]:
    """
    Full font-aware AI pipeline on a PDF document.

    Returns:
        {
            "text":            str   — final clean Unicode text
            "font_analysis":   dict  — from analyse_document_fonts()
            "pages":           int   — number of pages processed
            "raw_converted":   str   — text after mechanical conversion (pre-AI)
            "ai_applied":      bool  — whether LLM correction ran
        }
    """
    # ── Step 1: Font detection ──────────────────────────────────────────────
    logger.info("Step 1/3 — Analysing fonts in PDF …")
    font_analysis = analyse_document_fonts(pdf_bytes)
    logger.info("Font analysis: %s", font_analysis["summary"])

    # ── Step 2: Span-level raw conversion ──────────────────────────────────
    logger.info("Step 2/3 — Extracting and converting text spans …")
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        raise ValueError(f"Cannot open PDF: {exc}") from exc

    page_texts: list[str] = []
    try:
        for i in range(len(doc)):
            page = doc.load_page(i)
            page_text = _extract_page_font_aware(page)
            page_texts.append(page_text)
    finally:
        doc.close()

    raw_unicode = "\n\n".join(pt for pt in page_texts if pt.strip())
    raw_unicode = _cleanup(raw_unicode)

    if not raw_unicode.strip():
        return {
            "text": "[No text layer found in PDF.]",
            "font_analysis": font_analysis,
            "pages": len(page_texts),
            "raw_converted": "",
            "ai_applied": False,
        }

    # ── Step 3: AI semantic correction ─────────────────────────────────────
    logger.info("Step 3/3 — AI semantic understanding and correction …")
    final_text = _ai_correct(raw_unicode, model_name=model_name)
    ai_applied = final_text != raw_unicode

    logger.info(
        "Pipeline complete. Pages=%d, FontStrategy=%s, AIApplied=%s",
        len(page_texts), font_analysis["strategy"], ai_applied,
    )

    return {
        "text": final_text,
        "font_analysis": font_analysis,
        "pages": len(page_texts),
        "raw_converted": raw_unicode,
        "ai_applied": ai_applied,
    }
