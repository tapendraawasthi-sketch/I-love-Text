"""Tests for the raster sanitization pipeline helpers."""

from app.config import SANITIZE_DPI, SANITIZE_DPI_LARGE, SANITIZE_DPI_MEDIUM
from app.extract.raster_pipeline import sanitize_dpi_for_page_count


def test_sanitize_dpi_scales_down_for_large_documents():
    assert sanitize_dpi_for_page_count(5) == SANITIZE_DPI
    assert sanitize_dpi_for_page_count(80) == SANITIZE_DPI_MEDIUM
    assert sanitize_dpi_for_page_count(300) == SANITIZE_DPI_LARGE
    assert sanitize_dpi_for_page_count(300) < sanitize_dpi_for_page_count(5)
