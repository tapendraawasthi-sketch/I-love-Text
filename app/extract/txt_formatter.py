"""
TXT formatter: converts extraction results to a clean, human-readable .txt file.

Output format for each page:

  ════════════════════════════════════════════════════════════
  PAGE 3
  ════════════════════════════════════════════════════════════
  [HEADER]  नेपाल सरकार — अर्थ मन्त्रालय

  # बजेट वक्तव्य २०८१/८२

  ## खण्ड १: आर्थिक स्थिति समीक्षा

  विगत आर्थिक वर्षमा राजस्व संकलन ...

  ┌──────────────┬──────────────┬──────────────┐
  │ शीर्षक       │ रकम          │ प्रतिशत      │
  ├──────────────┼──────────────┼──────────────┤
  │ राजस्व       │ ₹ १,२०,०००  │ ४५%          │
  └──────────────┴──────────────┴──────────────┘

  [FOOTER]  पृष्ठ ३ / ४०

"""
from __future__ import annotations

import re

_MULTI_BLANK = re.compile(r"\n{4,}")


def _page_separator(page_num: int, total: int | None = None) -> str:
    label = f"  PAGE {page_num}" + (f" / {total}" if total else "")
    bar = "═" * 60
    return f"\n{bar}\n{label}\n{bar}\n"


def format_as_txt(
    extraction_result: dict,
    *,
    include_page_separators: bool = True,
    include_headers_footers: bool = True,
    include_quality_report: bool = False,
) -> str:
    """
    Convert an extraction result dict (from extract_pdf or extract_large_pdf_streaming)
    to a clean UTF-8 string suitable for saving as a .txt file.

    Parameters
    ----------
    extraction_result:
        The dict returned by extract_pdf() or extract_large_pdf_streaming().
    include_page_separators:
        Insert a visible separator line between pages.
    include_headers_footers:
        Include [HEADER] and [FOOTER] markers.
    include_quality_report:
        Append a quality summary section at the end of the file.

    Returns
    -------
    str — complete document text, UTF-8, ready to write to disk.
    """
    text: str = extraction_result.get("text", "")
    pages: int = extraction_result.get("pages", 0)

    if not text.strip():
        return "[No text could be extracted from this document.]\n"

    # The streaming extractor already inserts ─── page separators.
    # For the TXT export, replace them with the prettier ═══ separators.
    page_sep_re = re.compile(
        r"\n*─{30,}\n\s*Page\s+(\d+)\s*\n─{30,}\n*", re.IGNORECASE
    )

    if include_page_separators:
        counter = {"n": 0}

        def replace_sep(m: re.Match) -> str:
            counter["n"] += 1
            page_n = int(m.group(1))
            return _page_separator(page_n, pages if pages else None)

        text = page_sep_re.sub(replace_sep, text)
    else:
        text = page_sep_re.sub("\n\n", text)

    if not include_headers_footers:
        text = re.sub(r"\[HEADER\][^\n]*\n?", "", text)
        text = re.sub(r"\[FOOTER\][^\n]*\n?", "", text)

    # Normalise excessive blank lines
    text = _MULTI_BLANK.sub("\n\n\n", text)
    text = text.strip() + "\n"

    if include_quality_report:
        report = extraction_result.get("quality_report", [])
        if report:
            lines = ["\n\n" + "─" * 60, "EXTRACTION QUALITY REPORT", "─" * 60]
            for b in report:
                lines.append(
                    f"Batch {b['batch']} (pages {b['pages']}): "
                    f"direct_unicode={b.get('direct_unicode',0)}, "
                    f"direct_legacy={b.get('direct_legacy',0)}, "
                    f"no_text={b.get('no_text',0)}, "
                    f"chars={b.get('total_chars',0)}"
                )
            method = extraction_result.get("method", "")
            fonts = extraction_result.get("legacy_fonts", [])
            lines.append(f"\nMethod: {method}")
            if fonts:
                lines.append(f"Legacy fonts found: {', '.join(fonts)}")
            text += "\n".join(lines) + "\n"

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
