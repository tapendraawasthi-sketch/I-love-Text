"""
Structure builder: converts flat PDF text blocks into structured text.

Detects heading levels based on font size relative to the document's body
font size, then outputs text with Markdown-style heading markers.

For a Nepali government document the output looks like:

  # नेपाल सरकार
  ## आर्थिक वर्ष २०८१/८२
  ### खण्ड १ : सामान्य प्रावधानहरू

  धारा १ मा उल्लेखित व्यवस्था अनुसार ...

The heading markers (#, ##, ###) are preserved in the .txt output because
they are readable to humans and are also valid Markdown for downstream
AI ingestion.
"""
from __future__ import annotations

import re
import statistics
from typing import NamedTuple

from app.extract.zone_classifier import ZonedBlock


class HeadingLevel(NamedTuple):
    level: int          # 1, 2, or 3
    threshold: float    # minimum font size for this level


def compute_heading_thresholds(
    body_blocks: list[ZonedBlock],
) -> tuple[float, list[HeadingLevel]]:
    """
    Compute the body font size and heading thresholds from block data.

    Strategy:
    1. Collect all font sizes from body blocks.
    2. The body font size is the MODE (most frequent rounded size).
    3. Blocks larger than body+2pt are headings.
    4. Three bands: H1 (largest), H2 (medium), H3 (slightly above body).

    Returns
    -------
    (body_size, [HeadingLevel(1, t1), HeadingLevel(2, t2), HeadingLevel(3, t3)])
    """
    sizes = [b.font_size for b in body_blocks if b.font_size > 0]
    if not sizes:
        return 10.0, []

    # Round to nearest 0.5 to group similar sizes
    rounded = [round(s * 2) / 2 for s in sizes]

    # Mode = most frequent size = body text size
    from collections import Counter
    counts = Counter(rounded)
    body_size = counts.most_common(1)[0][0]

    # Collect distinct sizes above body
    above_body = sorted({s for s in rounded if s > body_size + 1.5}, reverse=True)

    if not above_body:
        return body_size, []

    if len(above_body) == 1:
        return body_size, [HeadingLevel(1, above_body[0])]

    if len(above_body) == 2:
        return body_size, [
            HeadingLevel(1, above_body[0]),
            HeadingLevel(2, above_body[1]),
        ]

    # 3 or more distinct sizes above body → map to H1/H2/H3
    # H1 = top third, H2 = middle third, H3 = bottom third
    q1 = above_body[0]
    mid = above_body[len(above_body) // 2]
    q3 = above_body[-1]
    return body_size, [
        HeadingLevel(1, q1),
        HeadingLevel(2, mid),
        HeadingLevel(3, q3),
    ]


def classify_block_as_heading(
    block: ZonedBlock,
    body_size: float,
    heading_levels: list[HeadingLevel],
) -> int:
    """Return heading level (1/2/3) or 0 for normal paragraph."""
    if not heading_levels or block.font_size <= 0:
        return 0

    fs = block.font_size

    # A block is a heading if it is bold OR its font size exceeds a threshold
    for level in sorted(heading_levels, key=lambda h: h.threshold, reverse=True):
        if fs >= level.threshold - 0.25:
            return level.level

    # Bold text at body size = H3 heading (common in Nepali docs)
    if block.is_bold and fs >= body_size - 0.5:
        return 3

    return 0


_BLANK_LINE_RE = re.compile(r"\n{3,}")


def build_structured_text(
    body_blocks: list[ZonedBlock],
    page_number: int | None = None,
) -> str:
    """
    Convert a list of body ZonedBlocks into structured text with heading markers.

    Parameters
    ----------
    body_blocks:
        Sorted (top-to-bottom) list of body blocks for one page.
    page_number:
        If provided, a page separator comment is inserted at the top.

    Returns
    -------
    str with #/##/### headings and paragraph text.
    """
    if not body_blocks:
        return ""

    body_size, heading_levels = compute_heading_thresholds(body_blocks)

    lines: list[str] = []

    if page_number is not None:
        lines.append(f"\n\n{'─' * 60}")
        lines.append(f"  Page {page_number}")
        lines.append(f"{'─' * 60}\n")

    for block in body_blocks:
        text = block.text.strip()
        if not text:
            continue

        h_level = classify_block_as_heading(block, body_size, heading_levels)
        if h_level == 1:
            lines.append(f"\n# {text}")
        elif h_level == 2:
            lines.append(f"\n## {text}")
        elif h_level == 3:
            lines.append(f"\n### {text}")
        else:
            lines.append(text)

    result = "\n".join(lines)
    # Collapse runs of 3+ blank lines to 2
    result = _BLANK_LINE_RE.sub("\n\n", result)
    return result


def build_document_structure(
    all_page_zones: list,   # list of PageZones from zone_classifier
    *,
    suppress_running: bool = True,
) -> str:
    """
    Build the full document structured text from all page zones.

    Parameters
    ----------
    all_page_zones:
        List of PageZones objects (one per page), in page order.
    suppress_running:
        If True, suppress running headers/footers (page numbers, doc titles
        that repeat across pages) to avoid clutter.

    Returns
    -------
    Full document as a single UTF-8 string.
    """
    from app.extract.zone_classifier import detect_running_header_footer

    running_headers, running_footers = (
        detect_running_header_footer(all_page_zones)
        if suppress_running
        else (set(), set())
    )

    parts: list[str] = []

    for pz in all_page_zones:
        page_parts: list[str] = []

        # Header zone
        header_text = pz.header_text()
        if header_text and header_text not in running_headers:
            page_parts.append(f"[HEADER] {header_text}")

        # Body zone with structure detection
        structured = build_structured_text(pz.body_blocks, page_number=pz.page_number)
        if structured.strip():
            page_parts.append(structured)

        # Footer zone
        footer_text = pz.footer_text()
        if footer_text and footer_text not in running_footers:
            page_parts.append(f"[FOOTER] {footer_text}")

        if page_parts:
            parts.append("\n".join(page_parts))

    return "\n\n".join(parts)
