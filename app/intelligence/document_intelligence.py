"""
Document Intelligence System — analysis-only layer.

The analyzer produces a JSON document describing structural properties
of every uploaded file BEFORE any text extraction runs.

Legacy dataclasses (DocumentIntelligenceResult, PageIntelligence, etc.)
are retained for backward compatibility with the extraction pipeline.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.intelligence.document_analyzer import analyze_document_bytes
from app.intelligence.page_segmenter import segment_document_bytes
from app.legacy_fonts.mappings import is_legacy_font
from app.logging_config import get_logger

logger = get_logger("DocumentIntelligence")


# ---------------------------------------------------------------------------
# Legacy enums / dataclasses (used by ocr_router and pdf_handler)
# ---------------------------------------------------------------------------


class DocumentFamily(Enum):
    NEPAL_GOVERNMENT_ACT = "nepal_government_act"
    NEPAL_GOVERNMENT_CIRCULAR = "nepal_government_circular"
    FINANCIAL_STATEMENT = "financial_statement"
    AUDIT_REPORT = "audit_report"
    TAX_DOCUMENT = "tax_document"
    BANK_STATEMENT = "bank_statement"
    INVOICE = "invoice"
    LEGAL_CONTRACT = "legal_contract"
    ACADEMIC_PAPER = "academic_paper"
    BOOK = "book"
    NEWSPAPER = "newspaper"
    FORM = "form"
    CERTIFICATE = "certificate"
    SCANNED_DOCUMENT = "scanned_document"
    MIXED_DOCUMENT = "mixed_document"
    UNKNOWN = "unknown"


class PageType(Enum):
    DIGITAL_UNICODE = "digital_unicode"
    DIGITAL_LEGACY = "digital_legacy"
    SCANNED_CLEAN = "scanned_clean"
    SCANNED_NOISY = "scanned_noisy"
    SCANNED_ROTATED = "scanned_rotated"
    IMAGE_ONLY = "image_only"
    MIXED = "mixed"
    BLANK = "blank"
    TABLE_HEAVY = "table_heavy"
    FORM_PAGE = "form_page"
    UNKNOWN = "unknown"


class RegionType(Enum):
    HEADER = "header"
    FOOTER = "footer"
    PAGE_NUMBER = "page_number"
    BODY_TEXT = "body_text"
    TABLE = "table"
    FIGURE = "figure"
    CAPTION = "caption"
    SIDEBAR = "sidebar"
    MARGIN_NOTE = "margin_note"
    FOOTNOTE = "footnote"
    LOGO = "logo"
    STAMP = "stamp"
    SIGNATURE = "signature"
    WATERMARK = "watermark"
    QR_CODE = "qr_code"
    HEADING = "heading"
    LIST = "list"
    EQUATION = "equation"
    EMPTY = "empty"


@dataclass
class PageRegion:
    region_type: RegionType
    bbox: tuple[float, float, float, float]
    confidence: float = 0.0
    text: str = ""
    extraction_strategy: str = ""
    reading_order: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class PageIntelligence:
    page_number: int
    page_type: PageType = PageType.UNKNOWN
    page_type_confidence: float = 0.0
    regions: list[PageRegion] = field(default_factory=list)
    fonts_detected: list[str] = field(default_factory=list)
    legacy_fonts: list[str] = field(default_factory=list)
    has_text_layer: bool = False
    text_layer_quality: float = 0.0
    visual_text_density: float = 0.0
    is_rotated: bool = False
    rotation_angle: float = 0.0
    column_count: int = 1
    has_tables: bool = False
    has_images: bool = False
    has_stamps: bool = False
    recommended_strategy: str = "direct"


@dataclass
class DocumentIntelligenceResult:
    family: DocumentFamily = DocumentFamily.UNKNOWN
    family_confidence: float = 0.0
    page_intelligence: list[PageIntelligence] = field(default_factory=list)
    total_pages: int = 0
    dominant_font_family: str = "unknown"
    dominant_encoding: str = "unknown"
    is_scanned: bool = False
    is_legacy: bool = False
    is_mixed: bool = False
    domain: str = "general"
    language_hint: str = "nep+eng"
    recommended_pipeline: str = "auto"
    processing_order: list[int] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def analyze_document_json(
    file_bytes: bytes,
    *,
    file_type: str = "pdf",
    mime_type: str | None = None,
    filename: str = "",
) -> dict[str, Any]:
    """
    Analyze a document and return complete JSON (no text content).

    This is the primary Document Intelligence entry point.
    """
    report = analyze_document_bytes(
        file_bytes,
        file_type=file_type,
        mime_type=mime_type,
        filename=filename,
    )
    logger.info(
        "Document analysis: type=%s pages=%d digital=%s scanned=%s mixed=%s "
        "dominant_lang=%s confidence=%.1f",
        report["document"]["file_type"],
        report["document"]["page_count"],
        report["document"]["digital_pdf"],
        report["document"]["scanned_pdf"],
        report["document"]["mixed_pdf"],
        report["document"].get("dominant_language"),
        report["document"]["confidence"],
    )
    return report


def segment_document_json(
    file_bytes: bytes,
    *,
    file_type: str = "pdf",
    mime_type: str | None = None,
    filename: str = "",
) -> dict[str, Any]:
    """
    Segment every page into semantic blocks (no OCR, no text content).

    Returns document metadata plus per-page block lists ready for
    region-based extraction.
    """
    report = segment_document_bytes(
        file_bytes,
        file_type=file_type,
        mime_type=mime_type,
        filename=filename,
    )
    logger.info(
        "Document segmentation: type=%s pages=%d blocks=%d",
        report["document"]["file_type"],
        report["document"]["page_count"],
        report["segmentation"]["total_blocks"],
    )
    return report


def analyze_document(pdf_bytes: bytes) -> DocumentIntelligenceResult:
    """
    Legacy adapter for the extraction pipeline.

    Runs the segmenter and maps block results to DocumentIntelligenceResult
    so pdf_handler and ocr_router continue to work unchanged.
    """
    report = segment_document_json(pdf_bytes, file_type="pdf")
    return _legacy_adapter_from_segment(report)


# ---------------------------------------------------------------------------
# Legacy mapping
# ---------------------------------------------------------------------------

_PAGE_CLASS_MAP = {
    "digital_unicode": PageType.DIGITAL_UNICODE,
    "digital_legacy": PageType.DIGITAL_LEGACY,
    "digital": PageType.DIGITAL_UNICODE,
    "scanned": PageType.SCANNED_CLEAN,
    "scanned_noisy": PageType.SCANNED_NOISY,
    "scanned_rotated": PageType.SCANNED_ROTATED,
    "scanned_image": PageType.SCANNED_CLEAN,
    "image_only": PageType.IMAGE_ONLY,
    "mixed": PageType.MIXED,
    "blank": PageType.BLANK,
    "table_heavy": PageType.TABLE_HEAVY,
}

_STRATEGY_MAP = {
    PageType.DIGITAL_UNICODE: "direct",
    PageType.DIGITAL_LEGACY: "direct_with_conversion",
    PageType.SCANNED_CLEAN: "ocr_standard",
    PageType.SCANNED_NOISY: "ocr_enhanced",
    PageType.SCANNED_ROTATED: "ocr_with_deskew",
    PageType.IMAGE_ONLY: "ocr_standard",
    PageType.MIXED: "hybrid",
    PageType.BLANK: "skip",
    PageType.TABLE_HEAVY: "table_extraction",
    PageType.FORM_PAGE: "form_extraction",
}


_BLOCK_TO_REGION: dict[str, RegionType] = {
    "heading": RegionType.HEADING,
    "paragraph": RegionType.BODY_TEXT,
    "list": RegionType.LIST,
    "table": RegionType.TABLE,
    "figure": RegionType.FIGURE,
    "caption": RegionType.CAPTION,
    "image": RegionType.FIGURE,
    "chart": RegionType.FIGURE,
    "formula": RegionType.EQUATION,
    "qr_code": RegionType.QR_CODE,
    "barcode": RegionType.FIGURE,
    "signature": RegionType.SIGNATURE,
    "stamp": RegionType.STAMP,
    "header": RegionType.HEADER,
    "footer": RegionType.FOOTER,
    "margin_note": RegionType.MARGIN_NOTE,
    "handwriting": RegionType.MARGIN_NOTE,
}

_BLOCK_TO_STRATEGY: dict[str, str] = {
    "table": "table_extraction",
    "qr_code": "skip",
    "barcode": "skip",
    "figure": "skip",
    "image": "skip",
    "chart": "skip",
    "stamp": "skip",
    "signature": "skip",
    "handwriting": "ocr_standard",
    "margin_note": "ocr_standard",
    "header": "direct",
    "footer": "direct",
    "heading": "direct",
    "list": "direct",
    "paragraph": "direct",
    "caption": "direct",
    "formula": "direct",
}


def _legacy_adapter_from_segment(report: dict[str, Any]) -> DocumentIntelligenceResult:
    doc = report["document"]
    pages_json = report["pages"]

    result = DocumentIntelligenceResult()
    result.total_pages = doc["page_count"]
    result.is_scanned = doc["scanned_pdf"]
    result.is_mixed = doc["mixed_pdf"]
    result.is_legacy = doc.get("statistics", {}).get("legacy_font_pages", 0) > 0
    result.dominant_font_family = doc.get("dominant_font") or "unknown"
    result.dominant_encoding = "legacy" if result.is_legacy else "unicode"
    result.language_hint = _language_hint(doc)
    result.recommended_pipeline = _recommend_pipeline(doc)
    result.metadata = {
        "analysis": doc,
        "segmentation": report.get("segmentation", {}),
    }

    if doc["scanned_pdf"]:
        result.family = DocumentFamily.SCANNED_DOCUMENT
        result.family_confidence = doc["confidence"]
    elif doc["mixed_pdf"]:
        result.family = DocumentFamily.MIXED_DOCUMENT
        result.family_confidence = doc["confidence"]
    else:
        result.family = DocumentFamily.UNKNOWN
        result.family_confidence = doc["confidence"]

    for page_data in pages_json:
        result.page_intelligence.append(_map_page_from_blocks(page_data))

    result.processing_order = _processing_order(result.page_intelligence)
    return result


def _map_page_from_blocks(page_data: dict[str, Any]) -> PageIntelligence:
    blocks = page_data.get("blocks", [])
    has_tables = any(b["type"] == "table" for b in blocks)
    has_images = any(b["type"] in ("image", "figure", "chart") for b in blocks)
    has_stamps = any(b["type"] == "stamp" for b in blocks)
    has_text = any(b.get("contains_text") for b in blocks)
    legacy_fonts = [
        b["font"] for b in blocks
        if b.get("font") and (
            b.get("encoding") == "legacy"
            or any(f.get("is_legacy") for f in b.get("fonts", []))
        )
    ]

    if has_text and not has_images:
        page_class = "digital_legacy" if legacy_fonts else "digital_unicode"
    elif not has_text and has_images:
        page_class = "scanned"
    else:
        page_class = "mixed"

    page_type = _PAGE_CLASS_MAP.get(page_class, PageType.MIXED)

    intel = PageIntelligence(page_number=page_data["page_number"])
    intel.page_type = page_type
    intel.page_type_confidence = 80.0
    intel.fonts_detected = sorted({b["font"] for b in blocks if b.get("font")})
    intel.legacy_fonts = sorted(set(legacy_fonts))
    intel.has_text_layer = has_text
    intel.text_layer_quality = 50.0 if has_text else 0.0
    intel.is_rotated = abs(page_data.get("skew_degrees", 0)) > 2.0
    intel.rotation_angle = page_data.get("page_rotation", 0)
    intel.column_count = page_data.get("number_of_columns", 1)
    intel.has_tables = has_tables
    intel.has_images = has_images
    intel.has_stamps = has_stamps
    intel.recommended_strategy = _STRATEGY_MAP.get(page_type, "hybrid")
    intel.regions = _blocks_to_page_regions(blocks, intel.recommended_strategy)
    return intel


def _blocks_to_page_regions(
    blocks: list[dict[str, Any]],
    default_strategy: str,
) -> list[PageRegion]:
    regions: list[PageRegion] = []
    for block in blocks:
        btype = block.get("type", "paragraph")
        region_type = _BLOCK_TO_REGION.get(btype, RegionType.BODY_TEXT)
        strategy = _BLOCK_TO_STRATEGY.get(btype, default_strategy)
        if block.get("contains_table"):
            strategy = "table_extraction"
        elif block.get("is_mixed_fonts") or block.get("encoding") == "mixed":
            strategy = "pdf_extraction"
        elif block.get("encoding") == "unicode":
            strategy = "direct"
        elif block.get("encoding") == "legacy":
            strategy = "direct_with_conversion"
        elif not block.get("contains_text") and block.get("contains_image"):
            strategy = "ocr_standard" if block.get("ocr_eligible") else "skip"

        regions.append(PageRegion(
            region_type=region_type,
            bbox=tuple(block["bbox"]),
            confidence=block.get("confidence", 70.0),
            extraction_strategy=strategy,
            reading_order=block.get("reading_order", 0),
            metadata={
                "block_id": block.get("id"),
                "block_type": btype,
                "language": block.get("language"),
                "font": block.get("font"),
                "fonts": block.get("fonts", []),
                "font_confidence": block.get("font_confidence", 0.0),
                "is_mixed_fonts": block.get("is_mixed_fonts", False),
                "encoding": block.get("encoding", "unknown"),
                "has_legacy_font": block.get("encoding") in ("legacy", "mixed") or any(
                    f.get("is_legacy") for f in block.get("fonts", [])
                ),
                "has_unicode_font": block.get("encoding") in ("unicode", "mixed") or any(
                    f.get("encoding") == "unicode" for f in block.get("fonts", [])
                ),
                "rotation": block.get("rotation", 0),
                "contains_text": block.get("contains_text", False),
                "contains_image": block.get("contains_image", False),
                "contains_table": block.get("contains_table", False),
                "_source": block.get("source"),
                "ocr_eligible": block.get("ocr_eligible", False),
            },
        ))
    return regions


def _legacy_adapter(report: dict[str, Any]) -> DocumentIntelligenceResult:
    doc = report["document"]
    pages_json = report["pages"]

    result = DocumentIntelligenceResult()
    result.total_pages = doc["page_count"]
    result.is_scanned = doc["scanned_pdf"]
    result.is_mixed = doc["mixed_pdf"]
    result.is_legacy = doc.get("statistics", {}).get("legacy_font_pages", 0) > 0
    result.dominant_font_family = doc.get("dominant_font") or "unknown"
    result.dominant_encoding = "legacy" if result.is_legacy else "unicode"
    result.language_hint = _language_hint(doc)
    result.recommended_pipeline = _recommend_pipeline(doc)
    result.metadata = {"analysis": doc}

    if doc["scanned_pdf"]:
        result.family = DocumentFamily.SCANNED_DOCUMENT
        result.family_confidence = doc["confidence"]
    elif doc["mixed_pdf"]:
        result.family = DocumentFamily.MIXED_DOCUMENT
        result.family_confidence = doc["confidence"]
    else:
        result.family = DocumentFamily.UNKNOWN
        result.family_confidence = doc["confidence"]

    for page_data in pages_json:
        result.page_intelligence.append(_map_page(page_data))

    result.processing_order = _processing_order(result.page_intelligence)
    return result


def _map_page(page_data: dict[str, Any]) -> PageIntelligence:
    page_class = page_data.get("page_classification", "mixed")
    page_type = _PAGE_CLASS_MAP.get(page_class, PageType.MIXED)

    intel = PageIntelligence(page_number=page_data["page_number"])
    intel.page_type = page_type
    intel.page_type_confidence = page_data.get("analysis_confidence", 0.0)
    intel.fonts_detected = page_data.get("fonts_detected", [])
    intel.legacy_fonts = (
        intel.fonts_detected if page_data.get("has_legacy_fonts") else []
    )
    intel.has_text_layer = page_data["embedded_text_coverage_percent"] >= 3.0
    intel.text_layer_quality = page_data["embedded_text_coverage_percent"]
    intel.visual_text_density = sum(
        r.get("area_percent", 0) for r in page_data.get("scanned_regions", [])
    )
    intel.is_rotated = abs(page_data.get("skew_degrees", 0)) > 2.0
    intel.rotation_angle = page_data.get("page_rotation", 0)
    intel.column_count = page_data.get("number_of_columns", 1)
    intel.has_tables = bool(page_data.get("tables"))
    intel.has_images = bool(page_data.get("image_regions"))
    intel.has_stamps = bool(page_data.get("stamps"))
    intel.recommended_strategy = _STRATEGY_MAP.get(page_type, "hybrid")
    intel.regions = _map_regions(page_data, intel.recommended_strategy)
    return intel


def _map_regions(page_data: dict[str, Any], strategy: str) -> list[PageRegion]:
    regions: list[PageRegion] = []
    order = 0

    def add(region_type: RegionType, items: list[dict], conf: float, strat: str) -> None:
        nonlocal order
        for item in items:
            bb = tuple(item["bbox"])
            regions.append(PageRegion(
                region_type=region_type,
                bbox=bb,
                confidence=item.get("confidence", conf),
                extraction_strategy=strat,
                reading_order=order,
                metadata=item.get("metadata", {}),
            ))
            order += 1

    add(RegionType.HEADER, page_data.get("headers", []), 72.0, strategy)
    add(RegionType.FOOTER, page_data.get("footers", []), 72.0, strategy)
    add(RegionType.TABLE, page_data.get("tables", []), 85.0, "table_extraction")
    add(RegionType.FIGURE, page_data.get("image_regions", []), 80.0, "skip")
    add(RegionType.STAMP, page_data.get("stamps", []), 50.0, "skip")
    add(RegionType.SIGNATURE, page_data.get("signatures", []), 50.0, "skip")
    add(RegionType.QR_CODE, page_data.get("qr_codes", []), 90.0, "skip")
    add(RegionType.MARGIN_NOTE, page_data.get("handwritten_notes", []), 42.0, "ocr_standard")

    for scanned in page_data.get("scanned_regions", []):
        regions.append(PageRegion(
            region_type=RegionType.BODY_TEXT,
            bbox=tuple(scanned["bbox"]),
            confidence=scanned.get("confidence", 75.0),
            extraction_strategy="ocr_standard",
            reading_order=order,
        ))
        order += 1

    regions.sort(key=lambda r: (r.bbox[1], r.bbox[0]))
    for i, region in enumerate(regions):
        region.reading_order = i
    return regions


def _language_hint(doc: dict[str, Any]) -> str:
    langs = doc.get("language") or []
    if "nep" in langs:
        return "nep+eng" if "eng" in langs else "nep"
    if "eng" in langs:
        return "eng"
    return "nep+eng"


def _recommend_pipeline(doc: dict[str, Any]) -> str:
    if doc.get("scanned_pdf"):
        return "ocr_full"
    stats = doc.get("statistics", {})
    if stats.get("legacy_font_pages", 0) > 0 and not doc.get("scanned_pdf"):
        return "direct_legacy_conversion"
    if doc.get("digital_pdf"):
        return "direct_unicode"
    return "hybrid"


def _processing_order(pages: list[PageIntelligence]) -> list[int]:
    def complexity(pi: PageIntelligence) -> int:
        score = 0
        if pi.has_tables:
            score += 10
        if pi.column_count > 1:
            score += 5
        if pi.page_type == PageType.SCANNED_NOISY:
            score += 8
        return score

    indices = list(range(len(pages)))
    indices.sort(key=lambda i: complexity(pages[i]), reverse=True)
    return indices
