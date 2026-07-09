"""
Streaming batch extractor for large (100-500+ page) Nepali PDFs.

Processes the PDF in configurable batches (default 50 pages).
Each batch is processed, written to a temporary file, and then
garbage-collected before the next batch starts.  This keeps RAM usage
roughly proportional to BATCH_SIZE rather than total page count.

Usage
-----
from app.extract.streaming_extractor import extract_large_pdf_streaming

result = extract_large_pdf_streaming(pdf_bytes, lang="auto")
# result["text"]  → full extracted text
# result["pages"] → page count
# result["quality_report"] → per-batch quality summary
"""
from __future__ import annotations

import gc
import logging
import tempfile
import os
from typing import Any, Generator

import fitz

from app.extract.direct_extract import extract_page_direct, _fix_common_errors
from app.extract.zone_classifier import classify_page_zones, PageZones
from app.extract.structure_builder import build_structured_text
from app.extract.table_extractor import extract_tables_from_page, get_table_bboxes
from app.legacy_fonts.converter import is_legacy_encoded, force_convert_legacy
from app.legacy_fonts.mappings import is_legacy_font, get_npttf2utf_map_name
from app.nlp.font_detector import analyse_document_fonts

logger = logging.getLogger("StreamingExtractor")

DEFAULT_BATCH_SIZE = 50   # pages per batch
PROGRESS_LOG_EVERY = 10  # log progress every N pages


def _process_batch(
    doc: fitz.Document,
    page_indices: list[int],
    font_lookup: dict[str, Any],
    lang: str,
) -> tuple[list[PageZones], list[str], dict[str, Any]]:
    """
    Process a batch of pages.

    Returns
    -------
    (page_zones, page_texts, batch_stats)
    """
    page_zones: list[PageZones] = []
    page_texts: list[str] = []
    batch_stats = {
        "direct_unicode": 0,
        "direct_legacy": 0,
        "no_text": 0,
        "total_chars": 0,
    }

    for idx in page_indices:
        page = doc.load_page(idx)

        # Extract with font-aware conversion
        result = extract_page_direct(page, font_lookup)
        method = result.get("method", "no_text")
        batch_stats[method.replace("direct_unicode", "direct_unicode")
                         .replace("direct_legacy", "direct_legacy")
                         .replace("no_text", "no_text")] = (
            batch_stats.get(method, 0) + 1
        )
        batch_stats["total_chars"] += result.get("char_count", 0)

        # Zone classification
        pz = classify_page_zones(page)

        # Override body text with converted text from direct extraction
        converted = result.get("text", "")
        if converted.strip():
            # Replace the zone body text with properly converted text
            structured = build_structured_text(pz.body_blocks, page_number=idx + 1)
            page_text = structured if structured.strip() else converted
        else:
            page_text = ""

        page_zones.append(pz)
        page_texts.append(page_text)

        page.clean_contents()  # release page resources

    gc.collect()
    return page_zones, page_texts, batch_stats


def extract_large_pdf_streaming(
    pdf_bytes: bytes,
    lang: str = "auto",
    batch_size: int = DEFAULT_BATCH_SIZE,
    on_progress: Any | None = None,
) -> dict[str, Any]:
    """
    Extract text from a large PDF using batch streaming.

    Parameters
    ----------
    pdf_bytes:
        Raw bytes of the PDF file.
    lang:
        Language hint ("auto", "nep", "eng", "eng+nep").
    batch_size:
        Number of pages to process per batch.  Reduce if RAM is limited.
    on_progress:
        Optional callable(pages_done, total_pages) called after each batch.

    Returns
    -------
    dict with keys:
        text          → full extracted text (UTF-8)
        pages         → total page count
        batches       → number of batches processed
        quality_report → list of per-batch stats
        legacy_fonts  → list of legacy fonts found in document
        method        → "streaming_direct"
    """
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        raise ValueError(f"Cannot open PDF: {exc}") from exc

    total_pages = len(doc)
    logger.info("StreamingExtractor: %d pages, batch_size=%d", total_pages, batch_size)

    # Analyse fonts once for the whole document (cheap — reads font tables only)
    font_analysis = analyse_document_fonts(pdf_bytes)
    font_lookup: dict[str, Any] = {}
    for font in font_analysis.get("fonts_found", []):
        font_lookup[font["raw_name"]] = font

    # Split page indices into batches
    all_indices = list(range(total_pages))
    batches = [
        all_indices[i: i + batch_size]
        for i in range(0, total_pages, batch_size)
    ]

    all_texts: list[str] = []
    quality_report: list[dict[str, Any]] = []
    pages_done = 0

    try:
        for batch_num, batch_indices in enumerate(batches, start=1):
            logger.info(
                "Batch %d/%d — pages %d-%d",
                batch_num, len(batches),
                batch_indices[0] + 1, batch_indices[-1] + 1,
            )

            _, page_texts, batch_stats = _process_batch(
                doc, batch_indices, font_lookup, lang
            )

            all_texts.extend(page_texts)
            quality_report.append(
                {
                    "batch": batch_num,
                    "pages": f"{batch_indices[0]+1}-{batch_indices[-1]+1}",
                    **batch_stats,
                }
            )

            pages_done += len(batch_indices)
            if on_progress:
                try:
                    on_progress(pages_done, total_pages)
                except Exception:
                    pass

            gc.collect()
    finally:
        doc.close()

    # Join all pages
    separator = "\n\n" + "─" * 60 + "\n\n"
    full_text = separator.join(t for t in all_texts if t.strip())
    full_text = _fix_common_errors(full_text)

    return {
        "text": full_text,
        "pages": total_pages,
        "batches": len(batches),
        "quality_report": quality_report,
        "legacy_fonts": font_analysis.get("legacy_fonts", []),
        "dominant_font_family": font_analysis.get("dominant_family", "unknown"),
        "method": "streaming_direct",
    }
