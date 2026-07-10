"""
Document Intelligence Engine — the master orchestrator.

Replaces the linear OCR→Repair→Return pipeline with:

    PDF
    ↓
    Document Intelligence (what kind of document?)
    ↓
    Page Intelligence (what's on each page?)
    ↓
    Region Intelligence (what are the regions?)
    ↓
    Element Intelligence (classify each element)
    ↓
    Font Intelligence (per-span font analysis)
    ↓
    Reading Order (graph-based)
    ↓
    Extraction (per-region strategy)
    ↓
    Repair (with character candidates)
    ↓
    Cross Validation (cross-page, cross-engine)
    ↓
    Semantic Validation (domain-aware)
    ↓
    Output

This addresses Problems 1, 2, 3, 4, 16, 17 from the architectural critique.
"""
from __future__ import annotations

import gc
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import fitz

from app.logging_config import get_logger

logger = get_logger("DocumentIntelligence")


class DocumentFamily(Enum):
    """High-level document classification."""
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
    """What kind of page is this?"""
    DIGITAL_UNICODE = "digital_unicode"         # Clean Unicode text layer
    DIGITAL_LEGACY = "digital_legacy"           # Legacy font text layer (Preeti etc.)
    SCANNED_CLEAN = "scanned_clean"             # Clean scan, good for OCR
    SCANNED_NOISY = "scanned_noisy"             # Noisy/degraded scan
    SCANNED_ROTATED = "scanned_rotated"         # Rotated scan
    IMAGE_ONLY = "image_only"                   # Photo/diagram, minimal text
    MIXED = "mixed"                             # Mix of digital text and images
    BLANK = "blank"                             # Empty or nearly empty page
    TABLE_HEAVY = "table_heavy"                 # Mostly tables
    FORM_PAGE = "form_page"                     # Form with fields


class RegionType(Enum):
    """What kind of region is this?"""
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
    """A detected region on a page with its classification."""
    region_type: RegionType
    bbox: tuple[float, float, float, float]
    confidence: float = 0.0
    text: str = ""
    extraction_strategy: str = ""  # What extraction method to use
    reading_order: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class PageIntelligence:
    """Complete intelligence about a single page."""
    page_number: int
    page_type: PageType = PageType.UNKNOWN
    page_type_confidence: float = 0.0
    regions: list[PageRegion] = field(default_factory=list)
    fonts_detected: list[str] = field(default_factory=list)
    legacy_fonts: list[str] = field(default_factory=list)
    has_text_layer: bool = False
    text_layer_quality: float = 0.0  # 0-100
    visual_text_density: float = 0.0  # Percentage of page with visible text
    is_rotated: bool = False
    rotation_angle: float = 0.0
    column_count: int = 1
    has_tables: bool = False
    has_images: bool = False
    has_stamps: bool = False
    recommended_strategy: str = "direct"  # direct, ocr, hybrid


@dataclass
class DocumentIntelligenceResult:
    """Complete intelligence about the entire document."""
    family: DocumentFamily = DocumentFamily.UNKNOWN
    family_confidence: float = 0.0
    page_intelligence: list[PageIntelligence] = field(default_factory=list)
    total_pages: int = 0
    dominant_font_family: str = "unknown"
    dominant_encoding: str = "unknown"
    is_scanned: bool = False
    is_legacy: bool = False
    is_mixed: bool = False
    domain: str = "general"  # legal, accounting, medical, etc.
    language_hint: str = "nep+eng"
    recommended_pipeline: str = "auto"
    processing_order: list[int] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


def analyze_document(pdf_bytes: bytes) -> DocumentIntelligenceResult:
    """
    Phase 1: Document-level intelligence BEFORE any extraction.

    This answers:
    - What kind of document is this?
    - What domain does it belong to?
    - What fonts are used?
    - Which pages are scanned vs digital?
    - What regions exist on each page?
    - What extraction strategy should each region use?
    """
    result = DocumentIntelligenceResult()

    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        raise ValueError(f"Cannot open PDF: {exc}") from exc

    result.total_pages = len(doc)

    try:
        # Step 1: Rapid font survey (all pages)
        font_survey = _survey_fonts(doc)
        result.dominant_font_family = font_survey["dominant_family"]
        result.dominant_encoding = font_survey["dominant_encoding"]
        result.is_legacy = font_survey["has_legacy"]
        result.metadata["font_survey"] = font_survey

        # Step 2: Per-page intelligence
        for idx in range(len(doc)):
            page = doc.load_page(idx)
            page_intel = _analyze_page(page, idx + 1, font_survey)
            result.page_intelligence.append(page_intel)
            gc.collect()

        # Step 3: Document family classification
        result.family, result.family_confidence = _classify_document_family(
            doc, result.page_intelligence, font_survey
        )

        # Step 4: Domain detection
        result.domain = _detect_domain(doc, result.page_intelligence)

        # Step 5: Determine if mostly scanned
        scanned_pages = sum(
            1 for pi in result.page_intelligence
            if pi.page_type in (
                PageType.SCANNED_CLEAN, PageType.SCANNED_NOISY,
                PageType.SCANNED_ROTATED, PageType.IMAGE_ONLY
            )
        )
        result.is_scanned = scanned_pages > len(doc) * 0.5

        # Step 6: Check for mixed content
        page_types = {pi.page_type for pi in result.page_intelligence}
        result.is_mixed = len(page_types) > 2

        # Step 7: Determine recommended pipeline
        result.recommended_pipeline = _recommend_pipeline(result)

        # Step 8: Determine processing order (critical pages first)
        result.processing_order = _determine_processing_order(result)

        # Step 9: Language hint
        result.language_hint = _determine_language(result)

    finally:
        doc.close()

    logger.info(
        "Document Intelligence: family=%s (%.0f%%), domain=%s, "
        "pages=%d, scanned=%s, legacy=%s, pipeline=%s",
        result.family.value, result.family_confidence,
        result.domain, result.total_pages,
        result.is_scanned, result.is_legacy,
        result.recommended_pipeline,
    )

    return result


def _survey_fonts(doc: fitz.Document) -> dict[str, Any]:
    """Quick survey of all fonts in the document."""
    from app.legacy_fonts.mappings import is_legacy_font
    from collections import Counter

    font_counts: Counter = Counter()
    legacy_fonts: set[str] = set()
    all_fonts: set[str] = set()

    # Sample up to 20 pages for speed
    sample_pages = min(len(doc), 20)
    step = max(1, len(doc) // sample_pages)

    for idx in range(0, len(doc), step):
        page = doc.load_page(idx)
        fonts = page.get_fonts(full=True)
        for font_info in fonts:
            font_name = font_info[3] or font_info[4] or ""
            if font_name:
                all_fonts.add(font_name)
                font_counts[font_name] += 1
                if is_legacy_font(font_name):
                    legacy_fonts.add(font_name)

    dominant = font_counts.most_common(1)[0][0] if font_counts else "unknown"

    return {
        "all_fonts": sorted(all_fonts),
        "legacy_fonts": sorted(legacy_fonts),
        "has_legacy": bool(legacy_fonts),
        "dominant_family": _classify_font_family(dominant),
        "dominant_encoding": "legacy" if is_legacy_font(dominant) else "unicode",
        "font_counts": dict(font_counts.most_common(10)),
    }


def _classify_font_family(font_name: str) -> str:
    """Map font name to family."""
    fl = font_name.lower()
    families = {
        "preeti": "preeti", "kantipur": "kantipur", "ekantipur": "kantipur",
        "sagarmatha": "sagarmatha", "himali": "himali", "aakriti": "aakriti",
        "mangal": "unicode", "kalimati": "unicode", "noto": "unicode",
        "times": "unicode", "arial": "unicode", "helvetica": "unicode",
    }
    for fragment, family in families.items():
        if fragment in fl:
            return family
    return "unknown"


def _analyze_page(
    page: fitz.Page,
    page_number: int,
    font_survey: dict[str, Any],
) -> PageIntelligence:
    """Analyze a single page to determine its type and regions."""
    import re
    from app.legacy_fonts.mappings import is_legacy_font

    intel = PageIntelligence(page_number=page_number)
    page_rect = page.rect

    # 1. Check text layer
    text = page.get_text("text", sort=True).strip()
    intel.has_text_layer = len(text) > 10

    # 2. Font analysis for this page
    page_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
    page_fonts: set[str] = set()
    page_legacy: set[str] = set()
    total_chars = 0
    devanagari_chars = 0

    for block in page_dict.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                fn = span.get("font", "")
                txt = span.get("text", "")
                if fn:
                    page_fonts.add(fn)
                    if is_legacy_font(fn):
                        page_legacy.add(fn)
                for c in txt:
                    if c.strip():
                        total_chars += 1
                        if "\u0900" <= c <= "\u097F":
                            devanagari_chars += 1

    intel.fonts_detected = sorted(page_fonts)
    intel.legacy_fonts = sorted(page_legacy)

    # 3. Visual density analysis
    try:
        pix = page.get_pixmap(dpi=72, alpha=False, colorspace=fitz.csGRAY)
        import numpy as np
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w)
        dark_pixels = np.sum(img < 128)
        intel.visual_text_density = dark_pixels / max(img.size, 1) * 100
        del pix, img
    except Exception:
        intel.visual_text_density = 0

    # 4. Table detection
    try:
        tabs = page.find_tables()
        intel.has_tables = bool(tabs and tabs.tables)
    except Exception:
        pass

    # 5. Image detection
    images = page.get_images(full=True)
    intel.has_images = len(images) > 0

    # 6. Classify page type
    if not intel.has_text_layer and intel.visual_text_density > 3:
        if intel.visual_text_density > 1:
            intel.page_type = PageType.SCANNED_CLEAN
        else:
            intel.page_type = PageType.IMAGE_ONLY
        intel.page_type_confidence = 85.0
    elif intel.has_text_layer and page_legacy:
        intel.page_type = PageType.DIGITAL_LEGACY
        intel.page_type_confidence = 90.0
    elif intel.has_text_layer and not page_legacy:
        deva_ratio = devanagari_chars / max(total_chars, 1)
        if deva_ratio > 0.3 or total_chars > 50:
            intel.page_type = PageType.DIGITAL_UNICODE
            intel.page_type_confidence = 95.0
        else:
            intel.page_type = PageType.MIXED
            intel.page_type_confidence = 60.0
    elif not intel.has_text_layer and intel.visual_text_density < 1:
        intel.page_type = PageType.BLANK
        intel.page_type_confidence = 90.0
    else:
        intel.page_type = PageType.MIXED
        intel.page_type_confidence = 50.0

    # 7. Recommend extraction strategy
    strategy_map = {
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
    intel.recommended_strategy = strategy_map.get(intel.page_type, "hybrid")

    # 8. Detect regions (simplified — full version would use layout detection)
    intel.regions = _detect_page_regions(page, page_dict, intel)

    # 9. Detect columns
    intel.column_count = _detect_column_count(page_dict, page_rect.width)

    return intel


def _detect_page_regions(
    page: fitz.Page,
    page_dict: dict,
    intel: PageIntelligence,
) -> list[PageRegion]:
    """Detect and classify regions on a page."""
    regions: list[PageRegion] = []
    page_height = page.rect.height
    page_width = page.rect.width

    # Header region (top 8%)
    header_limit = page_height * 0.08
    # Footer region (bottom 8%)
    footer_start = page_height * 0.92

    for block in page_dict.get("blocks", []):
        bbox = tuple(block.get("bbox", (0, 0, 0, 0)))
        mid_y = (bbox[1] + bbox[3]) / 2

        if block.get("type") == 1:
            # Image block
            regions.append(PageRegion(
                region_type=RegionType.FIGURE,
                bbox=bbox,
                confidence=80.0,
                extraction_strategy="skip",
            ))
            continue

        if block.get("type") != 0:
            continue

        # Classify by position
        text = ""
        font_size = 0.0
        is_bold = False
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text += span.get("text", "")
                if not font_size:
                    font_size = span.get("size", 0)
                    flags = span.get("flags", 0)
                    is_bold = bool(flags & (1 << 4))

        text = text.strip()
        if not text:
            continue

        import re
        if mid_y <= header_limit:
            region_type = RegionType.HEADER
        elif mid_y >= footer_start:
            # Check if page number
            if re.match(r"^\s*[\d\u0966-\u096F]+\s*$", text):
                region_type = RegionType.PAGE_NUMBER
            else:
                region_type = RegionType.FOOTER
        elif font_size > 14 and is_bold and len(text) < 120:
            region_type = RegionType.HEADING
        elif len(text) < 30 and re.match(
            r"^\s*[\d\u0966-\u096F]+[.)]\s", text
        ):
            region_type = RegionType.LIST
        else:
            region_type = RegionType.BODY_TEXT

        regions.append(PageRegion(
            region_type=region_type,
            bbox=bbox,
            confidence=70.0,
            text=text,
            extraction_strategy=intel.recommended_strategy,
        ))

    # Add table regions
    if intel.has_tables:
        try:
            tabs = page.find_tables()
            if tabs and tabs.tables:
                for tab in tabs.tables:
                    regions.append(PageRegion(
                        region_type=RegionType.TABLE,
                        bbox=tab.bbox,
                        confidence=85.0,
                        extraction_strategy="table_extraction",
                    ))
        except Exception:
            pass

    # Assign reading order
    regions.sort(key=lambda r: (r.bbox[1], r.bbox[0]))
    for i, region in enumerate(regions):
        region.reading_order = i

    return regions


def _detect_column_count(page_dict: dict, page_width: float) -> int:
    """Detect number of text columns on a page."""
    # Collect x-centers of all text blocks
    x_centers = []
    for block in page_dict.get("blocks", []):
        if block.get("type") != 0:
            continue
        bbox = block.get("bbox", (0, 0, 0, 0))
        center_x = (bbox[0] + bbox[2]) / 2
        width = bbox[2] - bbox[0]
        if width > page_width * 0.05:  # Skip tiny blocks
            x_centers.append(center_x)

    if len(x_centers) < 3:
        return 1

    # Check if x-centers cluster into distinct groups
    x_centers.sort()
    gaps = []
    for i in range(1, len(x_centers)):
        gaps.append(x_centers[i] - x_centers[i - 1])

    if not gaps:
        return 1

    # If there's a gap larger than 20% of page width, likely multi-column
    large_gaps = sum(1 for g in gaps if g > page_width * 0.2)
    if large_gaps >= 2:
        return 3
    elif large_gaps >= 1:
        return 2
    return 1


def _classify_document_family(
    doc: fitz.Document,
    pages: list[PageIntelligence],
    font_survey: dict[str, Any],
) -> tuple[DocumentFamily, float]:
    """Classify the document into a family."""
    import re

    # Read first few pages for classification
    first_pages_text = []
    for i in range(min(5, len(doc))):
        text = doc.load_page(i).get_text("text", sort=True)
        first_pages_text.append(text)

    combined = "\n".join(first_pages_text)
    scores: dict[DocumentFamily, float] = {f: 0.0 for f in DocumentFamily}

    # Legal Act patterns
    act_patterns = re.compile(
        r"(ऐन|नियमावली|विनियमावली|कानून|संविधान|Act|Ordinance|Regulation|Rule)",
        re.I,
    )
    if act_patterns.search(combined):
        scores[DocumentFamily.NEPAL_GOVERNMENT_ACT] += 40

    # Financial patterns
    fin_patterns = re.compile(
        r"(वासलात|नाफानोक्सान|Balance\s*Sheet|Profit|Loss|Income|Statement|"
        r"लेखापरीक्षण|Audit|Trial\s*Balance|Financial)",
        re.I,
    )
    if fin_patterns.search(combined):
        scores[DocumentFamily.FINANCIAL_STATEMENT] += 35

    # Tax patterns
    tax_patterns = re.compile(
        r"(कर|VAT|भ्याट|TDS|आयकर|Income\s*Tax|करयोग्य|Tax\s*Return)",
        re.I,
    )
    if tax_patterns.search(combined):
        scores[DocumentFamily.TAX_DOCUMENT] += 35

    # Invoice patterns
    inv_patterns = re.compile(
        r"(बीजक|Invoice|Bill|Receipt|रसीद|भुक्तानी|Payment)",
        re.I,
    )
    if inv_patterns.search(combined):
        scores[DocumentFamily.INVOICE] += 30

    # Bank statement patterns
    bank_patterns = re.compile(
        r"(Bank\s*Statement|खाता\s*विवरण|Account\s*Statement|बैंक)",
        re.I,
    )
    if bank_patterns.search(combined):
        scores[DocumentFamily.BANK_STATEMENT] += 30

    # Circular patterns
    circ_patterns = re.compile(
        r"(परिपत्र|Circular|Directive|Notice|सूचना|निर्देशन)",
        re.I,
    )
    if circ_patterns.search(combined):
        scores[DocumentFamily.NEPAL_GOVERNMENT_CIRCULAR] += 35

    # Legacy font bonus for government docs
    if font_survey["has_legacy"]:
        scores[DocumentFamily.NEPAL_GOVERNMENT_ACT] += 15
        scores[DocumentFamily.NEPAL_GOVERNMENT_CIRCULAR] += 10

    # Page count heuristics
    if len(doc) > 50:
        scores[DocumentFamily.NEPAL_GOVERNMENT_ACT] += 10
        scores[DocumentFamily.BOOK] += 10

    # Check if mostly scanned
    scanned = sum(
        1 for p in pages
        if p.page_type in (PageType.SCANNED_CLEAN, PageType.SCANNED_NOISY)
    )
    if scanned > len(pages) * 0.8:
        scores[DocumentFamily.SCANNED_DOCUMENT] += 20

    best = max(scores, key=scores.get)
    best_score = scores[best]

    if best_score < 15:
        return DocumentFamily.UNKNOWN, 10.0

    return best, min(100, best_score + 10)


def _detect_domain(
    doc: fitz.Document,
    pages: list[PageIntelligence],
) -> str:
    """Detect the domain of the document."""
    import re

    text = ""
    for i in range(min(3, len(doc))):
        text += doc.load_page(i).get_text("text", sort=True)

    domain_scores = {
        "legal": 0,
        "accounting": 0,
        "banking": 0,
        "taxation": 0,
        "government": 0,
        "education": 0,
        "general": 5,  # Default bias
    }

    legal_words = re.compile(
        r"(ऐन|नियम|दफा|उपदफा|अदालत|न्यायाधीश|मुद्दा|कानून|अभियुक्त|फैसला|"
        r"Section|Clause|Act|Court|Judge|Law|Regulation)"
    )
    accounting_words = re.compile(
        r"(खाता|जर्नल|खर्च|आम्दानी|नाफा|पूँजी|ऋण|Debit|Credit|Ledger|Journal|"
        r"Voucher|Balance|Asset|Liability|Revenue)"
    )
    banking_words = re.compile(
        r"(बैंक|ऋण|कर्जा|निक्षेप|ब्याज|Bank|Loan|Deposit|Interest|NRB|SEBON)"
    )
    tax_words = re.compile(
        r"(कर|भ्याट|VAT|TDS|आयकर|करयोग्य|Tax|Income\s*Tax|PAN)"
    )
    govt_words = re.compile(
        r"(सरकार|मन्त्रालय|विभाग|Government|Ministry|Department|Nepal)"
    )

    domain_scores["legal"] += len(legal_words.findall(text)) * 3
    domain_scores["accounting"] += len(accounting_words.findall(text)) * 3
    domain_scores["banking"] += len(banking_words.findall(text)) * 3
    domain_scores["taxation"] += len(tax_words.findall(text)) * 3
    domain_scores["government"] += len(govt_words.findall(text)) * 2

    return max(domain_scores, key=domain_scores.get)


def _recommend_pipeline(result: DocumentIntelligenceResult) -> str:
    """Recommend the best extraction pipeline."""
    if result.is_scanned:
        return "ocr_full"
    if result.is_legacy and not result.is_scanned:
        return "direct_legacy_conversion"
    if not result.is_legacy and not result.is_scanned:
        return "direct_unicode"
    return "hybrid"


def _determine_processing_order(result: DocumentIntelligenceResult) -> list[int]:
    """Determine optimal page processing order."""
    # Process pages with tables and complex layouts first (they benefit most
    # from fresh worker memory), then simpler pages
    pages = list(range(result.total_pages))

    def complexity(idx: int) -> int:
        if idx >= len(result.page_intelligence):
            return 0
        pi = result.page_intelligence[idx]
        score = 0
        if pi.has_tables:
            score += 10
        if pi.column_count > 1:
            score += 5
        if pi.page_type == PageType.SCANNED_NOISY:
            score += 8
        return score

    # Sort by complexity descending (hardest first while resources are fresh)
    pages.sort(key=complexity, reverse=True)
    return pages


def _determine_language(result: DocumentIntelligenceResult) -> str:
    """Determine the best language hint for OCR."""
    # If we detected Nepali content, always include nep
    has_devanagari = any(
        pi.page_type in (PageType.DIGITAL_UNICODE, PageType.DIGITAL_LEGACY)
        for pi in result.page_intelligence
    )
    if has_devanagari or result.is_legacy:
        return "nep+eng"
    return "eng"
