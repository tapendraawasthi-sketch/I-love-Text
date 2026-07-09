"""
Zone classifier for PDF pages.

Classifies text blocks into three zones:
  - HEADER  : top 8% of page height
  - FOOTER  : bottom 8% of page height
  - BODY    : everything in between

This enables the output formatter to write:
  [HEADER] <header text>
  <body text>
  [FOOTER] <footer text>

which preserves the reading order a human sees.

Also detects running headers/footers (same text repeating across pages) and
marks them for optional suppression in long document processing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import fitz

ZONE = Literal["header", "body", "footer"]

# Fraction of page height used as header/footer margin
HEADER_FRACTION = 0.08
FOOTER_FRACTION = 0.08


@dataclass
class ZonedBlock:
    zone: ZONE
    text: str
    bbox: tuple[float, float, float, float]
    font_size: float = 0.0
    font_name: str = ""
    is_bold: bool = False


@dataclass
class PageZones:
    page_number: int
    header_blocks: list[ZonedBlock] = field(default_factory=list)
    body_blocks: list[ZonedBlock] = field(default_factory=list)
    footer_blocks: list[ZonedBlock] = field(default_factory=list)

    def header_text(self) -> str:
        return " ".join(b.text for b in self.header_blocks).strip()

    def body_text(self) -> str:
        lines = []
        for b in self.body_blocks:
            lines.append(b.text)
        return "\n".join(lines)

    def footer_text(self) -> str:
        return " ".join(b.text for b in self.footer_blocks).strip()


def classify_page_zones(
    page: fitz.Page,
    converted_text_by_block: list[tuple[tuple, str]] | None = None,
) -> PageZones:
    """
    Classify all text blocks on a page into header / body / footer zones.

    Parameters
    ----------
    page:
        The fitz.Page object.
    converted_text_by_block:
        Optional list of (bbox, converted_text) pairs.  When provided, the
        converted text is used instead of the raw PDF text (important for
        legacy font pages where raw text is ASCII garbage).

    Returns
    -------
    PageZones with blocks sorted into the three zones.
    """
    page_rect = page.rect
    page_h = page_rect.height
    header_limit = page_rect.y0 + page_h * HEADER_FRACTION
    footer_start = page_rect.y1 - page_h * FOOTER_FRACTION

    # Build a lookup: bbox → converted text (if provided)
    converted_lookup: dict[tuple, str] = {}
    if converted_text_by_block:
        for bbox, text in converted_text_by_block:
            converted_lookup[tuple(round(v, 1) for v in bbox)] = text

    zones = PageZones(page_number=page.number + 1)

    page_dict = page.get_text("dict")
    for block in page_dict.get("blocks", []):
        if block.get("type") != 0:
            continue

        bbox = tuple(block.get("bbox", (0, 0, 0, 0)))
        y0 = bbox[1]
        y1 = bbox[3]
        block_mid_y = (y0 + y1) / 2.0

        # Get text (prefer converted version)
        lookup_key = tuple(round(v, 1) for v in bbox)
        if lookup_key in converted_lookup:
            text = converted_lookup[lookup_key]
        else:
            text = " ".join(
                span.get("text", "")
                for line in block.get("lines", [])
                for span in line.get("spans", [])
            ).strip()

        if not text:
            continue

        # Get dominant font size and name from first span
        font_size = 0.0
        font_name = ""
        is_bold = False
        try:
            first_span = block["lines"][0]["spans"][0]
            font_size = first_span.get("size", 0.0)
            font_name = first_span.get("font", "")
            flags = first_span.get("flags", 0)
            is_bold = bool(flags & 2**4)  # fitz bold flag
        except (IndexError, KeyError):
            pass

        zb = ZonedBlock(
            zone="body",
            text=text,
            bbox=bbox,
            font_size=font_size,
            font_name=font_name,
            is_bold=is_bold,
        )

        if block_mid_y <= header_limit:
            zb.zone = "header"
            zones.header_blocks.append(zb)
        elif block_mid_y >= footer_start:
            zb.zone = "footer"
            zones.footer_blocks.append(zb)
        else:
            zb.zone = "body"
            zones.body_blocks.append(zb)

    # Sort blocks within each zone top-to-bottom, then left-to-right
    key = lambda b: (b.bbox[1], b.bbox[0])
    zones.header_blocks.sort(key=key)
    zones.body_blocks.sort(key=key)
    zones.footer_blocks.sort(key=key)

    return zones


def detect_running_header_footer(
    all_page_zones: list[PageZones],
) -> tuple[set[str], set[str]]:
    """
    Detect running headers and footers that repeat across multiple pages.

    Returns
    -------
    (running_headers, running_footers)
        Sets of text strings that appear on more than 30% of pages.
        These are typically page numbers, document titles, chapter headings.
    """
    if not all_page_zones:
        return set(), set()

    from collections import Counter

    header_counts: Counter[str] = Counter()
    footer_counts: Counter[str] = Counter()

    for pz in all_page_zones:
        h = pz.header_text()
        f = pz.footer_text()
        if h:
            header_counts[h] += 1
        if f:
            footer_counts[f] += 1

    threshold = max(2, int(len(all_page_zones) * 0.30))
    running_headers = {t for t, c in header_counts.items() if c >= threshold}
    running_footers = {t for t, c in footer_counts.items() if c >= threshold}
    return running_headers, running_footers
