"""PDF embedded font name parsing (subset / CID). No other dependencies."""
from __future__ import annotations

import re
from typing import Any

_SUBSET_FONT_RE = re.compile(r"^[A-Z]{6}\+")
_CID_MARKERS = ("cid", "cidfont", "type0", "identity-h", "identity-v", "-cjk")


def parse_pdf_font_name(raw_name: str) -> dict[str, Any]:
    """
    Parse a PDF embedded font name.

    Subset fonts use ``SUBSET+BaseName`` (6 uppercase letters + plus).
    CID fonts often include ``CID``, ``Type0``, or ``Identity-H/V``.
    """
    raw = (raw_name or "").strip()
    is_subset = bool(_SUBSET_FONT_RE.match(raw))
    subset_id: str | None = None
    base_name = raw

    if is_subset and "+" in raw:
        subset_id, base_name = raw.split("+", 1)

    fl = base_name.lower()
    rl = raw.lower()
    is_cid = any(m in fl or m in rl for m in _CID_MARKERS)

    return {
        "raw_name": raw,
        "base_name": base_name,
        "normalized_name": base_name.lower().strip(),
        "subset_id": subset_id,
        "is_subset": is_subset,
        "is_cid": is_cid,
    }
