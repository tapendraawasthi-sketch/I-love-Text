"""
Intelligence module — Document Intelligence System.

Phase 1: analyze_document_json() — structural page analysis (no text).
Phase 2: segment_document_json() — semantic block segmentation (no OCR).
Phase 3+: region-based extraction uses legacy adapters.
"""

from app.intelligence.document_intelligence import (
    analyze_document,
    analyze_document_json,
    segment_document_json,
    DocumentFamily,
    DocumentIntelligenceResult,
    PageIntelligence,
    PageRegion,
    PageType,
    RegionType,
)

__all__ = [
    "analyze_document",
    "analyze_document_json",
    "segment_document_json",
    "DocumentFamily",
    "DocumentIntelligenceResult",
    "PageIntelligence",
    "PageRegion",
    "PageType",
    "RegionType",
]
