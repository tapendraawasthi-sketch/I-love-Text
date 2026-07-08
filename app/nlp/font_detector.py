"""
Font detection engine for PDF documents.

Analyses every font and text span in the uploaded PDF and determines:
  - Which Nepali font family is used (Preeti, Kantipur, Sagarmatha, Himali,
    Aakriti, Siddhi, PCS Nepali, Unicode, etc.)
  - Whether the font is a legacy ASCII-encoded Devanagari font or a true
    Unicode font
  - A confidence score for each detected font
  - A recommended conversion strategy per font

No OCR, no Tesseract — works entirely on the PDF text layer via PyMuPDF.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any

import fitz

from app.logging_config import get_logger

logger = get_logger("FontDetector")

# ---------------------------------------------------------------------------
# Known font registry
# ---------------------------------------------------------------------------

# Each entry: fragment (lowercase) → (display name, family, conversion_map)
FONT_REGISTRY: dict[str, dict[str, str]] = {
    # --- Preeti family ---
    "preeti":       {"name": "Preeti",        "family": "preeti",    "map": "preeti"},
    "ganess":       {"name": "Ganess",         "family": "preeti",    "map": "preeti"},
    "ganesh":       {"name": "Ganesh",         "family": "preeti",    "map": "preeti"},
    "fontasy":      {"name": "Fontasy Himali", "family": "preeti",    "map": "preeti"},
    "kanchan":      {"name": "Kanchan",        "family": "preeti",    "map": "preeti"},
    "navjeevan":    {"name": "Navjeevan",      "family": "preeti",    "map": "preeti"},
    "siddhi":       {"name": "Siddhi",         "family": "preeti",    "map": "preeti"},
    "vishwash":     {"name": "Vishwash",       "family": "preeti",    "map": "preeti"},

    # --- Kantipur family ---
    "kantipur":     {"name": "Kantipur",       "family": "kantipur",  "map": "kantipur"},
    "ekantipur":    {"name": "e-Kantipur",     "family": "kantipur",  "map": "kantipur"},

    # --- Sagarmatha ---
    "sagarmatha":   {"name": "Sagarmatha",     "family": "sagarmatha","map": "sagarmatha"},

    # --- Himali family ---
    "himali":       {"name": "Himali",         "family": "himali",    "map": "himali"},
    "himalb":       {"name": "Himali Bold",    "family": "himali",    "map": "himali"},

    # --- Aakriti ---
    "aakriti":      {"name": "Aakriti",        "family": "aakriti",   "map": "aakriti"},

    # --- PCS Nepali ---
    "pcsnepali":    {"name": "PCS Nepali",     "family": "pcsnepali", "map": "pcsnepali"},
    "pcs":          {"name": "PCS",            "family": "pcsnepali", "map": "pcsnepali"},

    # --- Kalimati (old encoding, treated as preeti-like) ---
    "kalimati_old": {"name": "Kalimati (Old)", "family": "preeti",    "map": "preeti"},

    # --- Unicode Devanagari fonts (no conversion needed) ---
    "mangal":       {"name": "Mangal",         "family": "unicode",   "map": None},
    "kalimati":     {"name": "Kalimati",       "family": "unicode",   "map": None},
    "lohit":        {"name": "Lohit Devanagari","family": "unicode",  "map": None},
    "noto":         {"name": "Noto Sans Devanagari","family":"unicode","map": None},
    "mukta":        {"name": "Mukta",          "family": "unicode",   "map": None},
    "tiro":         {"name": "Tiro Devanagari","family": "unicode",   "map": None},
    "yatra":        {"name": "Yatra One",      "family": "unicode",   "map": None},
    "laila":        {"name": "Laila",          "family": "unicode",   "map": None},
    "bitstreamvera":{"name": "Bitstream Vera", "family": "unicode",   "map": None},
    "arial":        {"name": "Arial Unicode",  "family": "unicode",   "map": None},
    "times":        {"name": "Times New Roman","family": "unicode",   "map": None},
    "helvetica":    {"name": "Helvetica",      "family": "unicode",   "map": None},
    "courier":      {"name": "Courier",        "family": "unicode",   "map": None},
}

_DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]")
_ASCII_ALPHA_RE = re.compile(r"[a-zA-Z]")


# ---------------------------------------------------------------------------
# Font identification helpers
# ---------------------------------------------------------------------------

def identify_font(font_name: str) -> dict[str, Any]:
    """
    Identify a font from its raw name as embedded in a PDF.

    Returns:
        {
            "raw_name": str,
            "matched_name": str,
            "family": "preeti" | "kantipur" | "sagarmatha" | "himali" |
                      "aakriti" | "pcsnepali" | "unicode" | "unknown",
            "is_legacy": bool,
            "conversion_map": str | None,
        }
    """
    fl = font_name.lower().strip()

    # Walk registry longest-fragment-first for specificity
    for fragment in sorted(FONT_REGISTRY, key=len, reverse=True):
        if fragment in fl:
            entry = FONT_REGISTRY[fragment]
            return {
                "raw_name": font_name,
                "matched_name": entry["name"],
                "family": entry["family"],
                "is_legacy": entry["family"] != "unicode",
                "conversion_map": entry["map"],
            }

    # Unknown — heuristic: if there is Devanagari in the font name itself → unicode
    if _DEVANAGARI_RE.search(font_name):
        return {
            "raw_name": font_name,
            "matched_name": font_name,
            "family": "unicode",
            "is_legacy": False,
            "conversion_map": None,
        }

    return {
        "raw_name": font_name,
        "matched_name": font_name,
        "family": "unknown",
        "is_legacy": False,
        "conversion_map": None,
    }


# ---------------------------------------------------------------------------
# Text-content heuristics
# ---------------------------------------------------------------------------

def _devanagari_ratio(text: str) -> float:
    chars = [c for c in text if c.strip()]
    if not chars:
        return 0.0
    return sum(1 for c in chars if _DEVANAGARI_RE.match(c)) / len(chars)


def _ascii_alpha_ratio(text: str) -> float:
    chars = [c for c in text if c.strip()]
    if not chars:
        return 0.0
    return sum(1 for c in chars if _ASCII_ALPHA_RE.match(c)) / len(chars)


# Common Preeti text-pattern indicators (from preeti_map.py's is_likely_preeti)
_PREETI_PATTERNS = [
    "sf", "cf", "of", "df", "jf", "tf", "xf",
    "s]", "g]", "b]", "x]", ";+", "k|", "cg", "/f",
    "sfo{", ";/sf/", "g]kfn",
]

# Common Kantipur pattern indicators (slightly different encoding)
_KANTIPUR_PATTERNS = [
    "sf", "cf", "of", "/f", "s]",
    "sfof{no", "lhNnf", "k|b]z",
]


def _score_preeti(text: str) -> int:
    return sum(1 for p in _PREETI_PATTERNS if p in text)


def _score_kantipur(text: str) -> int:
    return sum(1 for p in _KANTIPUR_PATTERNS if p in text)


def guess_font_from_text(text: str) -> dict[str, Any]:
    """
    When font name is 'unknown' or generic, guess the encoding from text content.

    Returns best-guess family + confidence (0–100).
    """
    if not text or not text.strip():
        return {"family": "unknown", "confidence": 0}

    deva = _devanagari_ratio(text)
    ascii_alpha = _ascii_alpha_ratio(text)

    # Already proper Unicode Devanagari
    if deva >= 0.40:
        return {"family": "unicode", "confidence": round(deva * 100)}

    # ASCII-heavy → likely a legacy font
    if ascii_alpha >= 0.25:
        preeti_score = _score_preeti(text)
        kantipur_score = _score_kantipur(text)
        if preeti_score > 0 or kantipur_score > 0:
            if kantipur_score > preeti_score:
                return {"family": "kantipur", "confidence": min(90, 50 + kantipur_score * 8)}
            return {"family": "preeti", "confidence": min(90, 50 + preeti_score * 8)}
        # High ASCII but no pattern — could still be a legacy font
        return {"family": "preeti", "confidence": 35}

    return {"family": "unknown", "confidence": 10}


# ---------------------------------------------------------------------------
# Full document analysis
# ---------------------------------------------------------------------------

def analyse_document_fonts(pdf_bytes: bytes) -> dict[str, Any]:
    """
    Open a PDF and collect every font used, their character coverage,
    and derive a per-document font strategy.

    Returns:
        {
            "fonts_found": [
                {
                    "raw_name": str,
                    "matched_name": str,
                    "family": str,
                    "is_legacy": bool,
                    "conversion_map": str | None,
                    "char_count": int,      # characters using this font
                    "confidence": int,      # 0-100 how sure we are
                }
            ],
            "dominant_family": str,          # most-used font family
            "dominant_map": str | None,      # npttf2utf map name for dominant
            "is_mixed": bool,                # multiple font families?
            "strategy": str,                 # "unicode_passthrough" | "legacy_convert" | "mixed" | "unknown"
            "summary": str,                  # human-readable one-liner
        }
    """
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        raise ValueError(f"Cannot open PDF: {exc}") from exc

    font_char_counts: Counter = Counter()       # raw_font_name → char count
    font_info_cache: dict[str, dict] = {}       # raw_font_name → identify_font result
    sample_texts: dict[str, list[str]] = {}     # raw_font_name → sample text list

    try:
        for page_num in range(len(doc)):
            page = doc.load_page(page_num)
            page_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)

            for block in page_dict.get("blocks", []):
                if block.get("type") != 0:
                    continue
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        font_name = span.get("font", "UnknownFont")
                        text = span.get("text", "")
                        if not text.strip():
                            continue

                        font_char_counts[font_name] += len(text)

                        if font_name not in font_info_cache:
                            font_info_cache[font_name] = identify_font(font_name)
                            sample_texts[font_name] = []

                        # Collect up to 200 chars as sample
                        if len("".join(sample_texts[font_name])) < 200:
                            sample_texts[font_name].append(text)
    finally:
        doc.close()

    if not font_char_counts:
        return {
            "fonts_found": [],
            "dominant_family": "unknown",
            "dominant_map": None,
            "is_mixed": False,
            "strategy": "unknown",
            "summary": "No text layer found in PDF.",
        }

    # Build enriched font list
    fonts_found: list[dict] = []
    family_weight: Counter = Counter()

    for raw_name, char_count in font_char_counts.most_common():
        info = font_info_cache[raw_name].copy()
        sample = "".join(sample_texts.get(raw_name, []))

        confidence = 90  # base confidence from registry match

        # If family is "unknown", use heuristic text analysis
        if info["family"] == "unknown" and sample:
            guess = guess_font_from_text(sample)
            info["family"] = guess["family"]
            confidence = guess["confidence"]
            if info["family"] != "unicode":
                info["is_legacy"] = True
                # Default to preeti map for unrecognised legacy
                info["conversion_map"] = info.get("conversion_map") or "preeti"

        info["char_count"] = char_count
        info["confidence"] = confidence
        fonts_found.append(info)

        family_weight[info["family"]] += char_count

    # Dominant family
    dominant_family = family_weight.most_common(1)[0][0] if family_weight else "unknown"
    dominant_map = next(
        (f["conversion_map"] for f in fonts_found
         if f["family"] == dominant_family and f.get("conversion_map")),
        None,
    )

    # Strategy
    unique_families = {f["family"] for f in fonts_found if f["family"] not in ("unknown",)}
    legacy_families = {f for f in unique_families if f not in ("unicode", "unknown")}

    if not unique_families:
        strategy = "unknown"
    elif not legacy_families:
        strategy = "unicode_passthrough"
    elif len(legacy_families) == 1 and "unicode" not in unique_families:
        strategy = "legacy_convert"
    else:
        strategy = "mixed"

    is_mixed = len(unique_families) > 1

    # Human readable summary
    font_names = ", ".join(
        f"{f['matched_name']} ({f['char_count']}ch)"
        for f in fonts_found[:5]
    )
    summary = (
        f"Detected {len(fonts_found)} font(s): {font_names}. "
        f"Dominant encoding: {dominant_family.upper()}. "
        f"Strategy: {strategy.replace('_', ' ')}."
    )

    logger.info("Font analysis: %s", summary)

    return {
        "fonts_found": fonts_found,
        "dominant_family": dominant_family,
        "dominant_map": dominant_map,
        "is_mixed": is_mixed,
        "strategy": strategy,
        "summary": summary,
    }
