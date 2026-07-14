"""
Document Analyzer — structure-only intelligence (no text extraction).

Every uploaded document is analyzed before any extraction pipeline runs.
Output is a JSON-serializable dict describing document-level and per-page
structural properties: fonts, scripts, regions, layout, and scan profile.
"""
from __future__ import annotations

import gc
from collections import Counter
from typing import Any

import cv2
import fitz
import numpy as np

from app.legacy_fonts.mappings import is_legacy_font
from app.logging_config import get_logger

logger = get_logger("DocumentAnalyzer")

# ---------------------------------------------------------------------------
# MIME / file-type registry
# ---------------------------------------------------------------------------

EXTENSION_MIME: dict[str, str] = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".tiff": "image/tiff",
    ".bmp": "image/bmp",
    ".webp": "image/webp",
}

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tiff", ".bmp", ".webp"}

# Script ranges (character codes counted internally — never returned as text)
_DEVANAGARI = range(0x0900, 0x0980)
_LATIN = range(0x0041, 0x007B)  # A-Z a-z subset via isalpha check

# Layout thresholds (fractions of page height / width)
_HEADER_FRAC = 0.08
_FOOTER_FRAC = 0.08
_MARGIN_FRAC = 0.05


def analyze_document_bytes(
    file_bytes: bytes,
    *,
    file_type: str,
    mime_type: str | None = None,
    filename: str = "",
) -> dict[str, Any]:
    """
    Analyze a document and return a complete JSON-ready structure.

    Does NOT extract or return document text content.
    """
    ext = file_type if file_type.startswith(".") else f".{file_type.lstrip('.')}"
    ext = ext.lower()
    resolved_mime = mime_type or EXTENSION_MIME.get(ext, "application/octet-stream")

    if ext in IMAGE_EXTENSIONS:
        return _analyze_image_document(file_bytes, ext, resolved_mime, filename)

    fitz_type = "pdf" if ext == ".pdf" else "docx" if ext == ".docx" else "pdf"
    try:
        doc = fitz.open(stream=file_bytes, filetype=fitz_type)
    except Exception as exc:
        raise ValueError(f"Cannot open document for analysis: {exc}") from exc

    try:
        font_survey = _survey_fonts(doc)
        pages: list[dict[str, Any]] = []
        script_totals: Counter[str] = Counter()

        for idx in range(len(doc)):
            page = doc.load_page(idx)
            page_report = _analyze_page(page, idx + 1, font_survey)
            pages.append(page_report)
            for lang, count in page_report.get("_script_counts", {}).items():
                script_totals[lang] += count
            del page_report["_script_counts"]
            if idx % 10 == 0:
                gc.collect()

        doc_level = _build_document_level(
            file_type=ext.lstrip("."),
            mime_type=resolved_mime,
            page_count=len(doc),
            pages=pages,
            font_survey=font_survey,
            script_totals=script_totals,
        )

        return {
            "document": doc_level,
            "pages": pages,
        }
    finally:
        doc.close()


def _analyze_image_document(
    file_bytes: bytes,
    ext: str,
    mime_type: str,
    filename: str,
) -> dict[str, Any]:
    """Single-page analysis for raster image uploads."""
    np_arr = np.frombuffer(file_bytes, np.uint8)
    img = cv2.imdecode(np_arr, cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise ValueError("Cannot decode image for analysis.")

    h, w = img.shape[:2]
    page_area = float(h * w)
    visual = _visual_mask(img)
    ink_pct = float(np.sum(visual)) / page_area * 100.0
    skew = _estimate_skew(img)

    image_regions = [_region_dict(0, 0, w, h, confidence=95.0, area_percent=100.0)]
    scanned_regions = (
        [_region_dict(0, 0, w, h, confidence=90.0, area_percent=ink_pct)]
        if ink_pct > 1.0
        else []
    )

    page = {
        "page_number": 1,
        "embedded_unicode_text": False,
        "embedded_text_coverage_percent": 0.0,
        "scanned_regions": scanned_regions,
        "image_regions": image_regions,
        "vector_graphics": [],
        "tables": [],
        "charts": [],
        "qr_codes": _detect_qr_codes(img),
        "barcodes": _detect_barcode_heuristic(img),
        "signatures": [],
        "stamps": [],
        "handwritten_notes": (
            [_region_dict(0, 0, w, h, confidence=35.0)]
            if ink_pct > 5.0
            else []
        ),
        "headers": [],
        "footers": [],
        "margins": _compute_margins_from_visual(visual, w, h),
        "page_rotation": 0,
        "skew_degrees": skew,
        "reading_direction": "ltr",
        "number_of_columns": 1,
        "page_classification": "scanned_image",
        "analysis_confidence": 80.0,
    }

    doc_level = {
        "file_type": ext.lstrip("."),
        "mime_type": mime_type,
        "filename": filename or None,
        "page_count": 1,
        "digital_pdf": False,
        "scanned_pdf": True,
        "mixed_pdf": False,
        "language": [],
        "dominant_language": None,
        "dominant_font": None,
        "confidence": 80.0,
    }

    return {"document": doc_level, "pages": [page]}


# ---------------------------------------------------------------------------
# Document-level aggregation
# ---------------------------------------------------------------------------


def _build_document_level(
    *,
    file_type: str,
    mime_type: str,
    page_count: int,
    pages: list[dict[str, Any]],
    font_survey: dict[str, Any],
    script_totals: Counter[str],
) -> dict[str, Any]:
    digital_pages = sum(
        1 for p in pages
        if p["embedded_text_coverage_percent"] >= 5.0
    )
    scanned_pages = sum(
        1 for p in pages
        if p["embedded_text_coverage_percent"] < 5.0
        and (
            len(p["scanned_regions"]) > 0
            or p["page_classification"] in ("scanned", "scanned_noisy", "image_only")
        )
    )
    mixed_pages = page_count - digital_pages - scanned_pages
    if mixed_pages < 0:
        mixed_pages = 0

    digital_ratio = digital_pages / max(page_count, 1)
    scanned_ratio = scanned_pages / max(page_count, 1)

    digital_pdf = digital_ratio >= 0.8
    scanned_pdf = scanned_ratio >= 0.8 and digital_ratio < 0.2
    mixed_pdf = not digital_pdf and not scanned_pdf

    languages = [lang for lang, _ in script_totals.most_common() if lang != "other"]
    dominant_language = script_totals.most_common(1)[0][0] if script_totals else None
    if dominant_language == "other":
        dominant_language = None

    page_confidences = [p.get("analysis_confidence", 0.0) for p in pages]
    doc_confidence = (
        round(sum(page_confidences) / len(page_confidences), 1)
        if page_confidences
        else 0.0
    )

    return {
        "file_type": file_type,
        "mime_type": mime_type,
        "page_count": page_count,
        "digital_pdf": digital_pdf,
        "scanned_pdf": scanned_pdf,
        "mixed_pdf": mixed_pdf,
        "language": languages,
        "dominant_language": dominant_language,
        "dominant_font": font_survey.get("dominant_font_name"),
        "confidence": doc_confidence,
        "statistics": {
            "digital_pages": digital_pages,
            "scanned_pages": scanned_pages,
            "mixed_pages": mixed_pages,
            "legacy_font_pages": sum(
                1 for p in pages if p.get("has_legacy_fonts")
            ),
            "pages_with_tables": sum(1 for p in pages if p["tables"]),
            "pages_with_images": sum(1 for p in pages if p["image_regions"]),
        },
    }


# ---------------------------------------------------------------------------
# Per-page analysis
# ---------------------------------------------------------------------------


def _analyze_page(
    page: fitz.Page,
    page_number: int,
    font_survey: dict[str, Any],
) -> dict[str, Any]:
    rect = page.rect
    page_w, page_h = rect.width, rect.height
    page_area = max(page_w * page_h, 1.0)

    page_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)

    text_blocks: list[tuple[float, float, float, float]] = []
    image_blocks: list[tuple[float, float, float, float]] = []
    script_counts: Counter[str] = Counter()
    has_unicode = False
    has_legacy_fonts = False
    text_block_area = 0.0

    for block in page_dict.get("blocks", []):
        bbox = tuple(block.get("bbox", (0, 0, 0, 0)))
        if block.get("type") == 1:
            image_blocks.append(bbox)
            continue
        if block.get("type") != 0:
            continue

        text_blocks.append(bbox)
        text_block_area += _bbox_area(bbox)

        for line in block.get("lines", []):
            for span in line.get("spans", []):
                font_name = span.get("font", "")
                if is_legacy_font(font_name):
                    has_legacy_fonts = True
                for char in span.get("text", ""):
                    script = _classify_char(char)
                    if script:
                        script_counts[script] += 1
                        if script == "nep" and not is_legacy_font(font_name):
                            has_unicode = True

    text_coverage = min(100.0, text_block_area / page_area * 100.0)

    # Visual / scan profile
    gray = _page_grayscale(page)
    visual = _visual_mask(gray)
    skew = _estimate_skew(gray)
    scanned_regions = _find_scanned_regions(visual, text_blocks, page_w, page_h)

    # PyMuPDF structural probes
    image_regions = _image_regions_from_blocks(image_blocks) + _image_regions_from_xref(page)
    vector_graphics = _extract_vector_graphics(page)
    tables = _detect_tables(page)
    charts = _detect_charts(page, tables, image_blocks, vector_graphics)
    qr_codes = _detect_qr_codes(gray)
    barcodes = _detect_barcode_heuristic(gray)

    headers, footers = _detect_headers_footers(text_blocks, page_h)
    margins = _compute_margins(text_blocks, image_blocks, page_w, page_h)
    signatures = _detect_signatures(image_blocks, vector_graphics, page_w, page_h)
    stamps = _detect_stamps(image_blocks, vector_graphics, page_w, page_h)
    handwritten_notes = _detect_handwritten_notes(
        vector_graphics, text_blocks, margins, page_w, page_h,
    )

    columns = _detect_column_count(text_blocks, page_w)
    rotation = int(page.rotation or 0)
    reading_dir = _detect_reading_direction(text_blocks, script_counts)

    page_class, class_conf = _classify_page(
        text_coverage=text_coverage,
        has_unicode=has_unicode,
        has_legacy_fonts=has_legacy_fonts,
        scanned_regions=scanned_regions,
        visual_ink_pct=float(np.sum(visual)) / max(visual.size, 1) * 100,
        skew=skew,
        tables=tables,
    )

    confidence = round(
        min(100.0, class_conf * 0.4 + text_coverage * 0.2 + (100 - abs(skew) * 5) * 0.2 + 20),
        1,
    )

    del gray, visual
    gc.collect()

    return {
        "page_number": page_number,
        "embedded_unicode_text": has_unicode and text_coverage >= 1.0,
        "embedded_text_coverage_percent": round(text_coverage, 2),
        "scanned_regions": scanned_regions,
        "image_regions": image_regions,
        "vector_graphics": vector_graphics,
        "tables": tables,
        "charts": charts,
        "qr_codes": qr_codes,
        "barcodes": barcodes,
        "signatures": signatures,
        "stamps": stamps,
        "handwritten_notes": handwritten_notes,
        "headers": headers,
        "footers": footers,
        "margins": margins,
        "page_rotation": rotation,
        "skew_degrees": round(skew, 2),
        "reading_direction": reading_dir,
        "number_of_columns": columns,
        "page_classification": page_class,
        "has_legacy_fonts": has_legacy_fonts,
        "fonts_detected": _page_fonts(page),
        "analysis_confidence": confidence,
        "_script_counts": dict(script_counts),
    }


# ---------------------------------------------------------------------------
# Detection helpers
# ---------------------------------------------------------------------------


def _region_dict(
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    *,
    confidence: float = 70.0,
    area_percent: float | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "bbox": [round(x0, 2), round(y0, 2), round(x1, 2), round(y1, 2)],
        "confidence": round(confidence, 1),
    }
    if area_percent is not None:
        out["area_percent"] = round(area_percent, 2)
    if metadata:
        out["metadata"] = metadata
    return out


def _bbox_area(bbox: tuple[float, ...]) -> float:
    return max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1])


def _classify_char(char: str) -> str | None:
    if not char.strip():
        return None
    cp = ord(char)
    if cp in _DEVANAGARI:
        return "nep"
    if char.isascii() and char.isalpha():
        return "eng"
    if char.isdigit():
        return "numeric"
    return "other"


def _survey_fonts(doc: fitz.Document) -> dict[str, Any]:
    font_counts: Counter[str] = Counter()
    legacy: set[str] = set()

    sample = min(len(doc), 20)
    step = max(1, len(doc) // sample)

    for idx in range(0, len(doc), step):
        page = doc.load_page(idx)
        for font_info in page.get_fonts(full=True):
            name = font_info[3] or font_info[4] or ""
            if name:
                font_counts[name] += 1
                if is_legacy_font(name):
                    legacy.add(name)

    dominant = font_counts.most_common(1)[0][0] if font_counts else None
    return {
        "dominant_font_name": dominant,
        "legacy_fonts": sorted(legacy),
        "has_legacy": bool(legacy),
        "font_counts": dict(font_counts.most_common(15)),
    }


def _page_fonts(page: fitz.Page) -> list[str]:
    names: set[str] = set()
    for font_info in page.get_fonts(full=True):
        name = font_info[3] or font_info[4] or ""
        if name:
            names.add(name)
    return sorted(names)


def _page_grayscale(page: fitz.Page, dpi: int = 72) -> np.ndarray:
    pix = page.get_pixmap(dpi=dpi, alpha=False, colorspace=fitz.csGRAY)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w)
    del pix
    return img


def _visual_mask(gray: np.ndarray) -> np.ndarray:
    """Binary mask of ink / visible content."""
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    return (binary > 0).astype(np.uint8)


def _estimate_skew(gray: np.ndarray) -> float:
    inverted = cv2.bitwise_not(gray)
    coords = np.column_stack(np.where(inverted > 128))
    if len(coords) < 50:
        return 0.0
    angle = cv2.minAreaRect(coords)[-1]
    if angle < -45:
        angle = -(90 + angle)
    else:
        angle = -angle
    if abs(angle) < 0.3 or abs(angle) > 15:
        return 0.0
    return round(angle, 2)


def _find_scanned_regions(
    visual: np.ndarray,
    text_blocks: list[tuple[float, ...]],
    page_w: float,
    page_h: float,
) -> list[dict[str, Any]]:
    """Areas with visible ink but no overlapping embedded text block."""
    h, w = visual.shape
    if h == 0 or w == 0:
        return []

    scale_x = w / max(page_w, 1)
    scale_y = h / max(page_h, 1)

    text_mask = np.zeros((h, w), dtype=np.uint8)
    for bbox in text_blocks:
        x0 = int(max(0, bbox[0] * scale_x))
        y0 = int(max(0, bbox[1] * scale_y))
        x1 = int(min(w, bbox[2] * scale_x))
        y1 = int(min(h, bbox[3] * scale_y))
        text_mask[y0:y1, x0:x1] = 1

    scanned_mask = visual.copy()
    scanned_mask[text_mask == 1] = 0

    if np.sum(scanned_mask) < 50:
        return []

    # Connected components on scanned ink
    n_labels, labels, stats, _ = cv2.connectedComponentsWithStats(
        scanned_mask, connectivity=8,
    )
    regions: list[dict[str, Any]] = []
    page_area = page_w * page_h

    for i in range(1, n_labels):
        x, y, bw, bh, area = stats[i]
        if area < 100:
            continue
        x0 = x / scale_x
        y0 = y / scale_y
        x1 = (x + bw) / scale_x
        y1 = (y + bh) / scale_y
        regions.append(
            _region_dict(
                x0, y0, x1, y1,
                confidence=75.0,
                area_percent=area / max(page_area * scale_x * scale_y, 1) * 100,
            )
        )

    return regions


def _image_regions_from_blocks(
    image_blocks: list[tuple[float, ...]],
) -> list[dict[str, Any]]:
    return [
        _region_dict(b[0], b[1], b[2], b[3], confidence=85.0)
        for b in image_blocks
    ]


def _image_regions_from_xref(page: fitz.Page) -> list[dict[str, Any]]:
    regions: list[dict[str, Any]] = []
    for img in page.get_images(full=True):
        xref = img[0]
        try:
            for rect in page.get_image_rects(xref):
                regions.append(
                    _region_dict(
                        rect.x0, rect.y0, rect.x1, rect.y1,
                        confidence=80.0,
                        metadata={"xref": xref},
                    )
                )
        except Exception:
            continue
    return regions


def _extract_vector_graphics(page: fitz.Page) -> list[dict[str, Any]]:
    regions: list[dict[str, Any]] = []
    try:
        drawings = page.get_drawings()
    except Exception:
        return regions

    for d in drawings:
        rect = d.get("rect")
        if rect is None:
            continue
        stroke_count = len(d.get("items", []))
        regions.append(
            _region_dict(
                rect.x0, rect.y0, rect.x1, rect.y1,
                confidence=70.0,
                metadata={"stroke_items": stroke_count},
            )
        )
    return regions


def _detect_tables(page: fitz.Page) -> list[dict[str, Any]]:
    regions: list[dict[str, Any]] = []
    try:
        tabs = page.find_tables()
        if tabs and tabs.tables:
            for tab in tabs.tables:
                bb = tab.bbox
                row_count = getattr(tab, "row_count", None)
                col_count = getattr(tab, "col_count", None)
                regions.append(
                    _region_dict(
                        bb[0], bb[1], bb[2], bb[3],
                        confidence=85.0,
                        metadata={
                            "row_count": row_count,
                            "col_count": col_count,
                        },
                    )
                )
    except Exception:
        pass
    return regions


def _detect_charts(
    page: fitz.Page,
    tables: list[dict[str, Any]],
    image_blocks: list[tuple[float, ...]],
    vectors: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Heuristic chart detection: large images or vector clusters with grid-like strokes."""
    charts: list[dict[str, Any]] = []
    table_bboxes = [t["bbox"] for t in tables]

    for bbox in image_blocks:
        w = bbox[2] - bbox[0]
        h = bbox[3] - bbox[1]
        aspect = w / max(h, 1)
        area = _bbox_area(bbox)
        if area < 2000:
            continue
        if 0.4 < aspect < 2.5 and not _overlaps_any(bbox, table_bboxes):
            charts.append(
                _region_dict(
                    bbox[0], bbox[1], bbox[2], bbox[3],
                    confidence=55.0,
                    metadata={"type": "image_chart_candidate"},
                )
            )

    for vec in vectors:
        bb = vec["bbox"]
        strokes = vec.get("metadata", {}).get("stroke_items", 0)
        w = bb[2] - bb[0]
        h = bb[3] - bb[1]
        if strokes >= 8 and w > 80 and h > 60 and not _overlaps_any(bb, table_bboxes):
            charts.append(
                _region_dict(
                    bb[0], bb[1], bb[2], bb[3],
                    confidence=50.0,
                    metadata={"type": "vector_chart_candidate", "strokes": strokes},
                )
            )

    return charts


def _detect_qr_codes(gray: np.ndarray) -> list[dict[str, Any]]:
    regions: list[dict[str, Any]] = []
    try:
        detector = cv2.QRCodeDetector()
        ok, points = detector.detect(gray)
        if ok and points is not None and len(points) > 0:
            for quad in points:
                xs = quad[:, 0]
                ys = quad[:, 1]
                regions.append(
                    _region_dict(
                        float(xs.min()), float(ys.min()),
                        float(xs.max()), float(ys.max()),
                        confidence=92.0,
                    )
                )
    except Exception:
        pass
    return regions


def _detect_barcode_heuristic(gray: np.ndarray) -> list[dict[str, Any]]:
    """Detect barcode-like horizontal stripe patterns (no decoder)."""
    regions: list[dict[str, Any]] = []
    h, w = gray.shape
    if h < 20 or w < 40:
        return regions

    # High horizontal gradient variance bands
    sobel_x = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
    row_energy = np.mean(np.abs(sobel_x), axis=1)
    threshold = np.percentile(row_energy, 92)
    band_rows = np.where(row_energy >= threshold)[0]
    if len(band_rows) < 3:
        return regions

    # Group consecutive rows
    groups: list[tuple[int, int]] = []
    start = band_rows[0]
    prev = band_rows[0]
    for r in band_rows[1:]:
        if r - prev > 3:
            groups.append((start, prev))
            start = r
        prev = r
    groups.append((start, prev))

    for y0, y1 in groups:
        band_h = y1 - y0 + 1
        if band_h < 5 or band_h > h * 0.25:
            continue
        aspect = w / max(band_h, 1)
        if aspect < 4:
            continue
        regions.append(
            _region_dict(
                0, float(y0), float(w), float(y1 + 1),
                confidence=45.0,
                metadata={"type": "barcode_candidate"},
            )
        )
    return regions


def _detect_headers_footers(
    text_blocks: list[tuple[float, ...]],
    page_h: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    header_limit = page_h * _HEADER_FRAC
    footer_start = page_h * (1.0 - _FOOTER_FRAC)

    headers: list[dict[str, Any]] = []
    footers: list[dict[str, Any]] = []

    for bbox in text_blocks:
        mid_y = (bbox[1] + bbox[3]) / 2.0
        if mid_y <= header_limit:
            headers.append(_region_dict(bbox[0], bbox[1], bbox[2], bbox[3], confidence=72.0))
        elif mid_y >= footer_start:
            footers.append(_region_dict(bbox[0], bbox[1], bbox[2], bbox[3], confidence=72.0))

    return headers, footers


def _compute_margins(
    text_blocks: list[tuple[float, ...]],
    image_blocks: list[tuple[float, ...]],
    page_w: float,
    page_h: float,
) -> dict[str, Any]:
    all_blocks = text_blocks + image_blocks
    if not all_blocks:
        return {
            "top": _margin_entry(0, 0, page_w, page_h * _MARGIN_FRAC),
            "bottom": _margin_entry(0, page_h * (1 - _MARGIN_FRAC), page_w, page_h),
            "left": _margin_entry(0, 0, page_w * _MARGIN_FRAC, page_h),
            "right": _margin_entry(page_w * (1 - _MARGIN_FRAC), 0, page_w, page_h),
        }

    min_x = min(b[0] for b in all_blocks)
    max_x = max(b[2] for b in all_blocks)
    min_y = min(b[1] for b in all_blocks)
    max_y = max(b[3] for b in all_blocks)

    return {
        "top": _margin_entry(0, 0, page_w, min_y),
        "bottom": _margin_entry(0, max_y, page_w, page_h),
        "left": _margin_entry(0, 0, min_x, page_h),
        "right": _margin_entry(max_x, 0, page_w, page_h),
    }


def _compute_margins_from_visual(
    visual: np.ndarray,
    w: int,
    h: int,
) -> dict[str, Any]:
    rows = np.any(visual, axis=1)
    cols = np.any(visual, axis=0)
    if not rows.any() or not cols.any():
        return {
            "top": _margin_entry(0, 0, w, h * _MARGIN_FRAC),
            "bottom": _margin_entry(0, h * (1 - _MARGIN_FRAC), w, h),
            "left": _margin_entry(0, 0, w * _MARGIN_FRAC, h),
            "right": _margin_entry(w * (1 - _MARGIN_FRAC), 0, w, h),
        }
    y_idx = np.where(rows)[0]
    x_idx = np.where(cols)[0]
    return {
        "top": _margin_entry(0, 0, w, float(y_idx[0])),
        "bottom": _margin_entry(0, float(y_idx[-1]), w, h),
        "left": _margin_entry(0, 0, float(x_idx[0]), h),
        "right": _margin_entry(float(x_idx[-1]), 0, w, h),
    }


def _margin_entry(x0: float, y0: float, x1: float, y1: float) -> dict[str, Any]:
    return {
        "bbox": [round(x0, 2), round(y0, 2), round(x1, 2), round(y1, 2)],
        "width_pt": round(max(0, x1 - x0), 2),
        "height_pt": round(max(0, y1 - y0), 2),
    }


def _detect_signatures(
    image_blocks: list[tuple[float, ...]],
    vectors: list[dict[str, Any]],
    page_w: float,
    page_h: float,
) -> list[dict[str, Any]]:
    sigs: list[dict[str, Any]] = []
    sig_zone_y = page_h * 0.65

    for bbox in image_blocks:
        w = bbox[2] - bbox[0]
        h = bbox[3] - bbox[1]
        if bbox[1] >= sig_zone_y and w < page_w * 0.45 and 10 < h < page_h * 0.2:
            sigs.append(
                _region_dict(bbox[0], bbox[1], bbox[2], bbox[3], confidence=50.0,
                             metadata={"type": "image_signature_candidate"}),
            )

    for vec in vectors:
        bb = vec["bbox"]
        w = bb[2] - bb[0]
        h = bb[3] - bb[1]
        strokes = vec.get("metadata", {}).get("stroke_items", 0)
        if bb[1] >= sig_zone_y and 30 < w < page_w * 0.4 and 10 < h < page_h * 0.15 and strokes >= 3:
            sigs.append(
                _region_dict(bb[0], bb[1], bb[2], bb[3], confidence=48.0,
                             metadata={"type": "vector_signature_candidate"}),
            )
    return sigs


def _detect_stamps(
    image_blocks: list[tuple[float, ...]],
    vectors: list[dict[str, Any]],
    page_w: float,
    page_h: float,
) -> list[dict[str, Any]]:
    stamps: list[dict[str, Any]] = []

    for bbox in image_blocks:
        w = bbox[2] - bbox[0]
        h = bbox[3] - bbox[1]
        if w < 20 or h < 20:
            continue
        aspect = w / max(h, 1)
        in_corner = (
            bbox[0] < page_w * 0.25 or bbox[2] > page_w * 0.75
        ) and (
            bbox[1] < page_h * 0.25 or bbox[3] > page_h * 0.75
        )
        if in_corner and 0.6 < aspect < 1.6 and w < page_w * 0.3:
            stamps.append(
                _region_dict(bbox[0], bbox[1], bbox[2], bbox[3], confidence=52.0,
                             metadata={"type": "stamp_image_candidate"}),
            )

    for vec in vectors:
        bb = vec["bbox"]
        w = bb[2] - bb[0]
        h = bb[3] - bb[1]
        aspect = w / max(h, 1)
        if 0.7 < aspect < 1.4 and 25 < w < page_w * 0.25 and 25 < h < page_h * 0.25:
            stamps.append(
                _region_dict(bb[0], bb[1], bb[2], bb[3], confidence=45.0,
                             metadata={"type": "stamp_vector_candidate"}),
            )
    return stamps


def _detect_handwritten_notes(
    vectors: list[dict[str, Any]],
    text_blocks: list[tuple[float, ...]],
    margins: dict[str, Any],
    page_w: float,
    page_h: float,
) -> list[dict[str, Any]]:
    notes: list[dict[str, Any]] = []
    margin_bboxes = [m["bbox"] for m in margins.values()]

    for vec in vectors:
        bb = vec["bbox"]
        strokes = vec.get("metadata", {}).get("stroke_items", 0)
        if strokes < 4:
            continue
        in_margin = any(_overlap_ratio(bb, mb) > 0.3 for mb in margin_bboxes)
        overlaps_text = any(_overlap_ratio(bb, tb) > 0.2 for tb in text_blocks)
        if in_margin and not overlaps_text:
            notes.append(
                _region_dict(bb[0], bb[1], bb[2], bb[3], confidence=42.0,
                             metadata={"type": "margin_ink_candidate"}),
            )
    return notes


def _detect_column_count(
    text_blocks: list[tuple[float, ...]],
    page_w: float,
) -> int:
    if len(text_blocks) < 3:
        return 1

    x_centers = sorted((b[0] + b[2]) / 2 for b in text_blocks if (b[2] - b[0]) > page_w * 0.05)
    if len(x_centers) < 3:
        return 1

    gaps = [x_centers[i] - x_centers[i - 1] for i in range(1, len(x_centers))]
    large = sum(1 for g in gaps if g > page_w * 0.18)
    if large >= 2:
        return 3
    if large >= 1:
        return 2
    return 1


def _detect_reading_direction(
    text_blocks: list[tuple[float, ...]],
    script_counts: Counter[str],
) -> str:
    if not text_blocks:
        return "ltr"
    # RTL hint: predominantly Arabic/Hebrew — not expected in Nepali corpus
    if script_counts.get("eng", 0) > script_counts.get("nep", 0) * 3:
        return "ltr"
    return "ltr"


def _classify_page(
    *,
    text_coverage: float,
    has_unicode: bool,
    has_legacy_fonts: bool,
    scanned_regions: list[dict[str, Any]],
    visual_ink_pct: float,
    skew: float,
    tables: list[dict[str, Any]],
) -> tuple[str, float]:
    if text_coverage < 1.0 and visual_ink_pct < 1.0:
        return "blank", 90.0
    if text_coverage < 3.0 and visual_ink_pct > 3.0:
        if abs(skew) > 2.0:
            return "scanned_rotated", 80.0
        if visual_ink_pct > 15.0:
            return "scanned_noisy", 75.0
        return "scanned", 85.0
    if has_legacy_fonts and text_coverage >= 3.0:
        return "digital_legacy", 88.0
    if has_unicode and text_coverage >= 5.0:
        return "digital_unicode", 92.0
    if text_coverage >= 3.0 and scanned_regions:
        return "mixed", 70.0
    if len(tables) >= 2 and text_coverage < 20.0:
        return "table_heavy", 78.0
    if text_coverage >= 3.0:
        return "digital", 75.0
    return "mixed", 55.0


def _overlaps_any(bbox: tuple[float, ...], others: list[list[float]]) -> bool:
    return any(_overlap_ratio(bbox, tuple(o)) > 0.3 for o in others)


def _overlap_ratio(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    x0 = max(a[0], b[0])
    y0 = max(a[1], b[1])
    x1 = min(a[2], b[2])
    y1 = min(a[3], b[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    inter = (x1 - x0) * (y1 - y0)
    area_a = _bbox_area(a)
    return inter / max(area_a, 1.0)
