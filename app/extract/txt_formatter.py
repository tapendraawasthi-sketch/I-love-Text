"""
TXT formatter: converts extraction results to a clean, human-readable .txt file.
"""
from __future__ import annotations

import re

_MULTI_BLANK = re.compile(r"\n{4,}")
_PAGE_BREAK_SIMPLE = re.compile(r"\n*\s*---\s*Page Break\s*---\s*\n*", re.IGNORECASE)
_PAGE_BREAK_STREAM = re.compile(
    r"\n*─{30,}\n\s*Page\s+(\d+)\s*\n─{30,}\n*",
    re.IGNORECASE,
)


def _page_separator(page_num: int, total: int | None = None) -> str:
    label = f"  PAGE {page_num}" + (f" / {total}" if total else "")
    bar = "═" * 60
    return f"{bar}\n{label}\n{bar}\n"


def _split_into_pages(text: str) -> list[str]:
    """Split extracted text on any known page-break marker."""
    # Normalize streaming markers to the simple marker first
    text = _PAGE_BREAK_STREAM.sub("\n\n--- Page Break ---\n\n", text)
    parts = _PAGE_BREAK_SIMPLE.split(text)
    return [p.strip() for p in parts if p is not None]


def format_as_txt(
    extraction_result: dict,
    *,
    include_page_separators: bool = True,
    include_headers_footers: bool = True,
    include_quality_report: bool = False,
    utf8_bom: bool = False,
) -> str:
    """
    Convert an extraction result dict to a clean UTF-8 string for .txt download.
    """
    text: str = extraction_result.get("text", "")
    pages: int = extraction_result.get("pages", 0)

    if not text.strip():
        return "[No text could be extracted from this document.]\n"

    page_parts = _split_into_pages(text)
    if not page_parts:
        page_parts = [text.strip()]

    if pages and pages > len(page_parts):
        # Prefer declared page count for separator labels when available
        total = pages
    else:
        total = len(page_parts) if len(page_parts) > 1 else (pages or None)

    if include_page_separators and len(page_parts) > 1:
        chunks: list[str] = []
        for i, part in enumerate(page_parts, start=1):
            chunks.append(_page_separator(i, total))
            chunks.append(part)
            chunks.append("")
        text = "\n".join(chunks).strip() + "\n"
    elif include_page_separators and len(page_parts) == 1:
        text = page_parts[0].strip() + "\n"
    else:
        text = "\n\n".join(page_parts).strip() + "\n"

    if not include_headers_footers:
        text = re.sub(r"\[HEADER\][^\n]*\n?", "", text)
        text = re.sub(r"\[FOOTER\][^\n]*\n?", "", text)

    text = _MULTI_BLANK.sub("\n\n\n", text)
    text = text.strip() + "\n"

    if include_quality_report:
        report = extraction_result.get("quality_report", [])
        lines = ["\n" + "─" * 60, "EXTRACTION QUALITY REPORT", "─" * 60]
        if report:
            for b in report:
                lines.append(
                    f"Batch {b['batch']} (pages {b['pages']}): "
                    f"direct_unicode={b.get('direct_unicode', 0)}, "
                    f"direct_legacy={b.get('direct_legacy', 0)}, "
                    f"no_text={b.get('no_text', 0)}, "
                    f"chars={b.get('total_chars', 0)}"
                )
        method = extraction_result.get("method", "")
        fidelity = extraction_result.get("fidelity", "")
        fonts = extraction_result.get("legacy_fonts", [])
        lines.append(f"\nMethod: {method}")
        if fidelity:
            lines.append(f"Fidelity: {fidelity}")
        if fonts:
            lines.append(f"Legacy fonts found: {', '.join(fonts)}")
        mean_conf = extraction_result.get("mean_confidence")
        if mean_conf is not None:
            lines.append(f"Mean confidence: {mean_conf}")
        corrections = extraction_result.get("corrections") or []
        if corrections:
            lines.append(f"Corrections applied: {len(corrections)}")
            for c in corrections[:50]:
                lines.append(f"  - {c.get('from')} → {c.get('to')} ({c.get('source')})")
        text += "\n".join(lines) + "\n"

    if utf8_bom:
        return "\ufeff" + text
    return text


def save_as_txt(
    extraction_result: dict,
    output_path: str,
    **kwargs,
) -> None:
    """Write extraction result to a .txt file at output_path."""
    content = format_as_txt(extraction_result, **kwargs)
    with open(output_path, "w", encoding="utf-8") as fh:
        fh.write(content)
