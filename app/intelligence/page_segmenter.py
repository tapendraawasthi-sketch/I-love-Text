"""
Page Segmenter — semantic block segmentation (no OCR, no text output).

Each page is decomposed into typed blocks with spatial and typographic
metadata. Blocks are returned before any extraction pipeline runs.
"""
from __future__ import annotations

import gc
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

import fitz

from app.intelligence import document_analyzer as da
from app.nlp.pdf_font_utils import analyse_span_font
from app.logging_config import get_logger

logger = get_logger("PageSegmenter")

# ---------------------------------------------------------------------------
# Block types (API output uses snake_case)
# ---------------------------------------------------------------------------

BLOCK_TYPES = frozenset({
    "heading",
    "paragraph",
    "list",
    "table",
    "figure",
    "caption",
    "image",
    "chart",
    "formula",
    "qr_code",
    "barcode",
    "signature",
    "stamp",
    "header",
    "footer",
    "margin_note",
    "handwriting",
})

# Overlap resolution priority (higher = kept when two blocks compete)
_TYPE_PRIORITY: dict[str, int] = {
    "table": 100,
    "qr_code": 95,
    "barcode": 94,
    "formula": 88,
    "chart": 85,
    "figure": 82,
    "image": 80,
    "stamp": 78,
    "signature": 76,
    "handwriting": 74,
    "header": 70,
    "footer": 70,
    "heading": 65,
    "list": 60,
    "caption": 58,
    "margin_note": 56,
    "paragraph": 50,
}

_HEADER_FRAC = 0.08
_FOOTER_FRAC = 0.08

_LIST_START_RE = re.compile(
    r"^[\s]*(?:"
    r"[\u0915-\u0939][\.\)]\s"
    r"|\([\u0915-\u0939]\)\s"
    r"|[\u0966-\u096F]+[\.\)]\s"
    r"|\d+[\.\)]\s"
    r"|[a-zA-Z][\.\)]\s"
    r"|\([a-zA-Z]\)\s"
    r"|\(\d+\)\s"
    r"|[ivxIVX]+[\.\)]\s"
    r"|[-•●○▪▸►]\s"
    r")"
)

_CAPTION_START_RE = re.compile(
    r"^(?:Figure|Fig\.?|Table|Chart|Graph|Diagram|Photo|Image|"
    r"छवि|तालिका|चित्र|सारणी)\s*[\u0966-\u096F\d]",
    re.I,
)

_EQUATION_FONT_FRAGMENTS = (
    "cambria math", "symbol", "stix", "asana math", "latin modern math",
)
_EQUATION_CHAR_RE = re.compile(r"[=<>≤≥≠±∓×÷∑∏∫∂√∞\\]")


@dataclass
class _BlockCandidate:
    type: str
    bbox: tuple[float, float, float, float]
    confidence: float
    rotation: int = 0
    language: str | None = None
    font: str | None = None
    fonts: list[dict[str, Any]] | None = None
    font_confidence: float = 0.0
    is_mixed_fonts: bool = False
    encoding: str = "unknown"
    contains_text: bool = False
    contains_image: bool = False
    contains_table: bool = False
    _source: str = ""

    def to_block(self, block_id: str, reading_order: int) -> dict[str, Any]:
        ocr_eligible = (
            self._source == "scanned"
            or (self.contains_image and not self.contains_text)
            or (self.type == "handwriting" and self.contains_image)
        )
        return {
            "id": block_id,
            "type": self.type,
            "bbox": [round(v, 2) for v in self.bbox],
            "confidence": round(self.confidence, 1),
            "reading_order": reading_order,
            "rotation": self.rotation,
            "language": self.language,
            "font": self.font,
            "fonts": self.fonts or [],
            "font_confidence": round(self.font_confidence, 1),
            "is_mixed_fonts": self.is_mixed_fonts,
            "encoding": self.encoding,
            "contains_text": self.contains_text,
            "contains_image": self.contains_image,
            "contains_table": self.contains_table,
            "source": self._source or None,
            "ocr_eligible": ocr_eligible,
        }


@dataclass
class _TextBlockMetrics:
    bbox: tuple[float, float, float, float]
    char_count: int
    dominant_font: str | None
    dominant_language: str | None
    font_details: list[dict[str, Any]]
    font_confidence: float
    is_mixed_fonts: bool
    encoding: str
    max_font_size: float
    body_font_size: float
    is_bold: bool
    line_count: int
    list_line_ratio: float
    has_equation_chars: bool
    is_equation_font: bool
    first_line_preview: str = ""


def segment_document_bytes(
    file_bytes: bytes,
    *,
    file_type: str,
    mime_type: str | None = None,
    filename: str = "",
) -> dict[str, Any]:
    """
    Segment every page into semantic blocks. No OCR, no text in output.
    """
    ext = file_type if file_type.startswith(".") else f".{file_type.lstrip('.')}"
    ext = ext.lower()
    resolved_mime = mime_type or da.EXTENSION_MIME.get(ext, "application/octet-stream")

    if ext in da.IMAGE_EXTENSIONS:
        return _segment_image_document(file_bytes, ext, resolved_mime, filename)

    fitz_type = "pdf" if ext == ".pdf" else "docx" if ext == ".docx" else "pdf"
    try:
        doc = fitz.open(stream=file_bytes, filetype=fitz_type)
    except Exception as exc:
        raise ValueError(f"Cannot open document for segmentation: {exc}") from exc

    try:
        analysis = da.analyze_document_bytes(
            file_bytes,
            file_type=file_type,
            mime_type=mime_type,
            filename=filename,
        )
        pages_out: list[dict[str, Any]] = []

        for idx in range(len(doc)):
            page = doc.load_page(idx)
            page_analysis = analysis["pages"][idx]
            blocks = _segment_page(page, idx + 1, page_analysis)
            pages_out.append({
                "page_number": idx + 1,
                "page_rotation": page_analysis.get("page_rotation", 0),
                "skew_degrees": page_analysis.get("skew_degrees", 0.0),
                "number_of_columns": page_analysis.get("number_of_columns", 1),
                "block_count": len(blocks),
                "blocks": blocks,
            })
            if idx % 10 == 0:
                gc.collect()

        return {
            "document": analysis["document"],
            "pages": pages_out,
            "segmentation": {
                "version": "1.0",
                "total_blocks": sum(p["block_count"] for p in pages_out),
                "extraction_pending": True,
            },
        }
    finally:
        doc.close()


def _segment_image_document(
    file_bytes: bytes,
    ext: str,
    mime_type: str,
    filename: str,
) -> dict[str, Any]:
    analysis = da.analyze_document_bytes(
        file_bytes, file_type=ext.lstrip("."), mime_type=mime_type, filename=filename,
    )
    page_a = analysis["pages"][0]
    h_info = page_a.get("image_regions", [{}])
    bbox = tuple(h_info[0].get("bbox", [0, 0, 100, 100])) if h_info else (0, 0, 100, 100)

    block = _BlockCandidate(
        type="image",
        bbox=bbox,
        confidence=90.0,
        contains_image=True,
        contains_text=False,
        _source="raster",
    ).to_block("p001_b001", 0)

    return {
        "document": analysis["document"],
        "pages": [{
            "page_number": 1,
            "page_rotation": 0,
            "skew_degrees": page_a.get("skew_degrees", 0.0),
            "number_of_columns": 1,
            "block_count": 1,
            "blocks": [block],
        }],
        "segmentation": {
            "version": "1.0",
            "total_blocks": 1,
            "extraction_pending": True,
        },
    }


def _segment_page(
    page: fitz.Page,
    page_number: int,
    page_analysis: dict[str, Any],
) -> list[dict[str, Any]]:
    page_h = page.rect.height
    page_w = page.rect.width
    page_rotation = int(page_analysis.get("page_rotation", 0) or 0)
    skew = float(page_analysis.get("skew_degrees", 0.0) or 0.0)

    candidates: list[_BlockCandidate] = []

    # --- Text-layer blocks (structure only) ---
    page_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
    body_size = _estimate_body_font_size(page_dict)
    text_bboxes: list[tuple[float, ...]] = []

    for block in page_dict.get("blocks", []):
        if block.get("type") != 0:
            continue
        metrics = _metrics_from_text_block(block, body_size)
        if metrics.char_count < 1:
            continue
        text_bboxes.append(metrics.bbox)
        block_type = _classify_text_block(metrics, page_h, page_w)
        candidates.append(_candidate_from_text_metrics(
            metrics, block_type, page_rotation, skew,
        ))

    # --- Typed regions from page analysis ---
    _add_region_candidates(candidates, page_analysis, page_rotation, skew)

    # --- Captions below figures/images ---
    candidates.extend(_detect_captions(candidates, page_dict, body_size, page_rotation, skew))

    # --- Scanned ink without text layer → paragraph blocks ---
    for region in page_analysis.get("scanned_regions", []):
        bb = tuple(region["bbox"])
        if _overlaps_any_bbox(bb, text_bboxes, threshold=0.35):
            continue
        candidates.append(_BlockCandidate(
            type="paragraph",
            bbox=bb,
            confidence=region.get("confidence", 70.0),
            rotation=page_rotation,
            language=page_analysis.get("reading_direction", "ltr"),
            font=None,
            contains_text=False,
            contains_image=True,
            _source="scanned",
        ))

    # --- Deduplicate overlapping blocks ---
    merged = _resolve_overlaps(candidates)

    # --- Reading order + IDs ---
    merged.sort(key=lambda b: (b.bbox[1], b.bbox[0]))
    blocks: list[dict[str, Any]] = []
    for order, cand in enumerate(merged):
        block_id = f"p{page_number:03d}_b{order + 1:03d}"
        blocks.append(cand.to_block(block_id, order))

    return blocks


def _estimate_body_font_size(page_dict: dict) -> float:
    sizes: list[float] = []
    for block in page_dict.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                sz = float(span.get("size", 0))
                if sz > 0:
                    sizes.append(sz)
    if not sizes:
        return 12.0
    return float(Counter(round(s, 1) for s in sizes).most_common(1)[0][0])


def _metrics_from_text_block(block: dict, body_font_size: float) -> _TextBlockMetrics:
    bbox = tuple(block.get("bbox", (0, 0, 0, 0)))
    font_counts: Counter[str] = Counter()
    font_span_map: dict[str, dict[str, Any]] = {}
    script_counts: Counter[str] = Counter()
    max_size = 0.0
    is_bold = False
    char_count = 0
    line_previews: list[str] = []
    equation_chars = 0
    equation_font = False
    list_lines = 0
    line_count = 0

    for line in block.get("lines", []):
        line_count += 1
        line_text_parts: list[str] = []
        for span in line.get("spans", []):
            text = span.get("text", "")
            font_name = span.get("font", "") or ""
            size = float(span.get("size", 0))
            flags = int(span.get("flags", 0))

            if size > max_size:
                max_size = size
            if flags & (1 << 4):
                is_bold = True

            fl = font_name.lower()
            if any(f in fl for f in _EQUATION_FONT_FRAGMENTS):
                equation_font = True

            for ch in text:
                if not ch.strip():
                    continue
                char_count += 1
                font_counts[font_name] += 1
                if font_name not in font_span_map:
                    font_span_map[font_name] = analyse_span_font(span)
                script = da._classify_char(ch)
                if script and script != "numeric":
                    script_counts[script] += 1
                if _EQUATION_CHAR_RE.search(ch):
                    equation_chars += 1
                line_text_parts.append(ch)

        line_str = "".join(line_text_parts).strip()
        if line_str:
            line_previews.append(line_str)
            if _LIST_START_RE.match(line_str):
                list_lines += 1

    dominant_font = font_counts.most_common(1)[0][0] if font_counts else None
    dominant_lang = script_counts.most_common(1)[0][0] if script_counts else None
    if dominant_lang == "numeric" and script_counts:
        dominant_lang = script_counts.most_common(2)[-1][0] if len(script_counts) > 1 else None

    total_font_chars = sum(font_counts.values()) or 1
    font_details: list[dict[str, Any]] = []
    weighted_conf = 0.0
    families: set[str] = set()
    encodings: set[str] = set()

    for fn, count in font_counts.most_common():
        span_info = font_span_map.get(fn) or analyse_span_font({"font": fn, "text": ""})
        entry = {
            **span_info,
            "char_count": count,
            "share_percent": round(count / total_font_chars * 100, 1),
        }
        font_details.append(entry)
        families.add(span_info.get("family", "unknown"))
        encodings.add(span_info.get("encoding", "unknown"))
        weighted_conf += span_info.get("confidence", 70) * count

    has_legacy = any(f.get("is_legacy") for f in font_details)
    has_unicode = any(f.get("encoding") == "unicode" for f in font_details)
    is_mixed = (
        len(families) > 1
        or (has_legacy and has_unicode)
        or len(font_details) > 1 and len(families) > 1
    )
    if has_legacy and has_unicode:
        encoding = "mixed"
    elif has_legacy:
        encoding = "legacy"
    elif has_unicode:
        encoding = "unicode"
    else:
        encoding = "unknown"

    return _TextBlockMetrics(
        bbox=bbox,
        char_count=char_count,
        dominant_font=dominant_font,
        dominant_language=dominant_lang,
        font_details=font_details,
        font_confidence=round(weighted_conf / total_font_chars, 1),
        is_mixed_fonts=is_mixed,
        encoding=encoding,
        max_font_size=max_size,
        body_font_size=body_font_size,
        is_bold=is_bold,
        line_count=line_count,
        list_line_ratio=list_lines / max(line_count, 1),
        has_equation_chars=equation_chars >= 2,
        is_equation_font=equation_font,
        first_line_preview=line_previews[0] if line_previews else "",
    )


def _classify_text_block(metrics: _TextBlockMetrics, page_h: float, page_w: float) -> str:
    mid_y = (metrics.bbox[1] + metrics.bbox[3]) / 2.0
    mid_x = (metrics.bbox[0] + metrics.bbox[2]) / 2.0

    if mid_y <= page_h * _HEADER_FRAC:
        return "header"
    if mid_y >= page_h * (1.0 - _FOOTER_FRAC):
        return "footer"

    # Margin notes: narrow blocks in side margins
    block_w = metrics.bbox[2] - metrics.bbox[0]
    in_left_margin = metrics.bbox[2] < page_w * 0.18 and block_w < page_w * 0.22
    in_right_margin = metrics.bbox[0] > page_w * 0.82 and block_w < page_w * 0.22
    if in_left_margin or in_right_margin:
        return "margin_note"

    if metrics.is_equation_font or (
        metrics.has_equation_chars and metrics.char_count < 80
    ):
        return "formula"

    if metrics.list_line_ratio >= 0.5 or (
        metrics.line_count >= 2 and metrics.list_line_ratio >= 0.34
    ):
        return "list"

    if _CAPTION_START_RE.match(metrics.first_line_preview):
        return "caption"

    if (
        metrics.max_font_size >= metrics.body_font_size * 1.15
        and (metrics.is_bold or metrics.max_font_size >= metrics.body_font_size * 1.35)
        and metrics.char_count < 200
    ):
        return "heading"

    return "paragraph"


def _candidate_from_text_metrics(
    metrics: _TextBlockMetrics,
    block_type: str,
    page_rotation: int,
    skew: float,
) -> _BlockCandidate:
    rotation = page_rotation
    if abs(skew) > 2.0:
        rotation = int(round(page_rotation + skew))

    return _BlockCandidate(
        type=block_type,
        bbox=metrics.bbox,
        confidence=_confidence_for_text_type(block_type, metrics),
        rotation=rotation,
        language=metrics.dominant_language,
        font=metrics.dominant_font,
        fonts=metrics.font_details,
        font_confidence=metrics.font_confidence,
        is_mixed_fonts=metrics.is_mixed_fonts,
        encoding=metrics.encoding,
        contains_text=True,
        contains_image=False,
        contains_table=False,
        _source="text_layer",
    )


def _confidence_for_text_type(block_type: str, metrics: _TextBlockMetrics) -> float:
    base = {
        "header": 72.0,
        "footer": 72.0,
        "heading": 78.0,
        "list": 75.0,
        "caption": 70.0,
        "formula": 68.0,
        "margin_note": 68.0,
        "paragraph": 82.0,
    }.get(block_type, 75.0)
    if metrics.char_count > 20:
        base = min(95.0, base + 5.0)
    return base


def _add_region_candidates(
    candidates: list[_BlockCandidate],
    page_analysis: dict[str, Any],
    page_rotation: int,
    skew: float,
) -> None:
    mapping: list[tuple[str, str, bool, bool, bool]] = [
        ("tables", "table", False, False, True),
        ("qr_codes", "qr_code", False, True, False),
        ("barcodes", "barcode", False, True, False),
        ("charts", "chart", False, True, False),
        ("image_regions", "image", False, True, False),
        ("signatures", "signature", False, True, False),
        ("stamps", "stamp", False, True, False),
        ("handwritten_notes", "handwriting", False, True, False),
    ]

    for key, btype, has_text, has_image, has_table in mapping:
        for region in page_analysis.get(key, []):
            rotation = page_rotation
            if abs(skew) > 2.0 and btype in ("handwriting", "paragraph"):
                rotation = int(round(page_rotation + skew))
            contains_text = has_text
            if btype == "handwriting":
                contains_text = False
            candidates.append(_BlockCandidate(
                type=btype,
                bbox=tuple(region["bbox"]),
                confidence=region.get("confidence", 70.0),
                rotation=rotation,
                language=None,
                font=None,
                contains_text=contains_text,
                contains_image=has_image,
                contains_table=has_table,
                _source=key,
            ))

    # Vector graphics → figure unless already chart
    for vec in page_analysis.get("vector_graphics", []):
        bb = tuple(vec["bbox"])
        if _candidate_overlaps_type(candidates, bb, {"chart", "formula"}):
            continue
        strokes = vec.get("metadata", {}).get("stroke_items", 0)
        if strokes >= 12:
            candidates.append(_BlockCandidate(
                type="figure",
                bbox=bb,
                confidence=vec.get("confidence", 65.0),
                rotation=page_rotation,
                contains_image=True,
                _source="vector",
            ))


def _detect_captions(
    existing: list[_BlockCandidate],
    page_dict: dict,
    body_size: float,
    page_rotation: int,
    skew: float,
) -> list[_BlockCandidate]:
    figures = [c for c in existing if c.type in ("figure", "image", "chart")]
    if not figures:
        return []

    captions: list[_BlockCandidate] = []
    for block in page_dict.get("blocks", []):
        if block.get("type") != 0:
            continue
        metrics = _metrics_from_text_block(block, body_size)
        if not _CAPTION_START_RE.match(metrics.first_line_preview):
            continue
        if metrics.char_count > 300:
            continue
        cap_bbox = metrics.bbox
        cap_cy = (cap_bbox[1] + cap_bbox[3]) / 2.0
        for fig in figures:
            fb = fig.bbox
            fig_bottom = fb[3]
            if cap_bbox[1] >= fig_bottom - 5 and cap_bbox[1] < fig_bottom + 80:
                if _x_overlap(cap_bbox, fb) > 0.3:
                    captions.append(_candidate_from_text_metrics(
                        metrics, "caption", page_rotation, skew,
                    ))
                    break
    return captions


def _resolve_overlaps(candidates: list[_BlockCandidate]) -> list[_BlockCandidate]:
    if not candidates:
        return []

    kept: list[_BlockCandidate] = []
    for cand in sorted(
        candidates,
        key=lambda c: (_TYPE_PRIORITY.get(c.type, 0), c.confidence),
        reverse=True,
    ):
        if any(_iou(cand.bbox, k.bbox) > 0.55 for k in kept):
            continue
        if any(
            _overlap_ratio(cand.bbox, k.bbox) > 0.65
            and _TYPE_PRIORITY.get(k.type, 0) >= _TYPE_PRIORITY.get(cand.type, 0)
            for k in kept
        ):
            continue
        kept.append(cand)
    return kept


def _iou(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    x0 = max(a[0], b[0])
    y0 = max(a[1], b[1])
    x1 = min(a[2], b[2])
    y1 = min(a[3], b[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    inter = (x1 - x0) * (y1 - y0)
    union = da._bbox_area(a) + da._bbox_area(b) - inter
    return inter / max(union, 1.0)


def _overlap_ratio(inner: tuple[float, ...], outer: tuple[float, ...]) -> float:
    x0 = max(inner[0], outer[0])
    y0 = max(inner[1], outer[1])
    x1 = min(inner[2], outer[2])
    y1 = min(inner[3], outer[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    inter = (x1 - x0) * (y1 - y0)
    return inter / max(da._bbox_area(inner), 1.0)


def _x_overlap(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    x0 = max(a[0], b[0])
    x1 = min(a[2], b[2])
    if x1 <= x0:
        return 0.0
    return (x1 - x0) / max(min(a[2] - a[0], b[2] - b[0]), 1.0)


def _overlaps_any_bbox(
    bbox: tuple[float, ...],
    others: list[tuple[float, ...]],
    threshold: float = 0.4,
) -> bool:
    return any(_overlap_ratio(bbox, o) > threshold for o in others)


def _candidate_overlaps_type(
    candidates: list[_BlockCandidate],
    bbox: tuple[float, ...],
    types: set[str],
) -> bool:
    for c in candidates:
        if c.type in types and _iou(c.bbox, bbox) > 0.4:
            return True
    return False
