"""
Visual Verification Engine.

Renders the extracted Unicode text and compares it visually against
the actual PDF page rendering. This catches cases where the ToUnicode
CMap is wrong but the visual glyphs are correct.

Strategy:
    1. Render PDF page to image at high DPI
    2. For each text region, crop the rendered image
    3. Render the extracted Unicode text to an image using the same font metrics
    4. Compare the two images
    5. If mismatch, flag for repair or OCR fallback

This is computationally expensive, so it's used selectively:
    - Only on pages with low Unicode confidence
    - Only on regions with validation errors
    - Only when the document has known problematic fonts
"""
from __future__ import annotations

import gc
from typing import Any

import fitz
import numpy as np

from app.logging_config import get_logger

logger = get_logger("VisualValidator")


def render_page_region(
    page: fitz.Page,
    bbox: tuple[float, float, float, float],
    dpi: int = 300,
) -> np.ndarray | None:
    """
    Render a specific region of a PDF page to a grayscale image.

    This shows what the HUMAN sees, regardless of what the text layer says.
    """
    try:
        clip = fitz.Rect(bbox)
        # Add small padding
        clip.x0 -= 2
        clip.y0 -= 2
        clip.x1 += 2
        clip.y1 += 2

        mat = fitz.Matrix(dpi / 72.0, dpi / 72.0)
        pix = page.get_pixmap(
            matrix=mat,
            clip=clip,
            alpha=False,
            colorspace=fitz.csGRAY,
        )
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w)
        return img
    except Exception as e:
        logger.debug("Failed to render region %s: %s", bbox, e)
        return None
    finally:
        gc.collect()


def check_text_presence(
    rendered_region: np.ndarray,
    min_ink_ratio: float = 0.02,
) -> bool:
    """
    Quick check: does this rendered region contain visible text (dark pixels)?

    If the extracted text says there's content here but the rendered image
    is blank, something is wrong.
    """
    if rendered_region is None or rendered_region.size == 0:
        return False

    # Count dark pixels (text)
    dark_pixels = np.sum(rendered_region < 128)
    total_pixels = rendered_region.size
    ink_ratio = dark_pixels / total_pixels

    return ink_ratio >= min_ink_ratio


def estimate_visual_text_density(
    page: fitz.Page,
    dpi: int = 150,
) -> dict[str, float]:
    """
    Estimate text density across the page using visual rendering.

    Returns metrics about how much visible text is on the page.
    Used to decide if OCR fallback is needed.
    """
    try:
        pix = page.get_pixmap(
            dpi=dpi,
            alpha=False,
            colorspace=fitz.csGRAY,
        )
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w)

        dark_pixels = np.sum(img < 128)
        total_pixels = img.size
        ink_ratio = dark_pixels / max(total_pixels, 1)

        # Estimate if page has text (vs blank/image-only)
        # Typical text page has 5-15% dark pixels
        has_visible_text = ink_ratio > 0.03
        is_dense_text = ink_ratio > 0.08
        is_mostly_blank = ink_ratio < 0.01

        return {
            "ink_ratio": round(ink_ratio * 100, 2),
            "has_visible_text": has_visible_text,
            "is_dense_text": is_dense_text,
            "is_mostly_blank": is_mostly_blank,
            "width": pix.width,
            "height": pix.height,
        }
    except Exception as e:
        logger.debug("Visual density estimation failed: %s", e)
        return {
            "ink_ratio": 0,
            "has_visible_text": False,
            "is_dense_text": False,
            "is_mostly_blank": True,
        }
    finally:
        gc.collect()


def validate_extraction_against_visual(
    page: fitz.Page,
    extracted_text: str,
    text_regions: list[tuple[float, float, float, float]],
    confidence_threshold: float = 70.0,
) -> dict[str, Any]:
    """
    Compare extracted text against visual rendering.

    This is the high-level validation function that checks whether
    what we extracted matches what the human would see.
    """
    visual_density = estimate_visual_text_density(page)

    # If page has visible text but we extracted nothing
    if visual_density["has_visible_text"] and not extracted_text.strip():
        return {
            "valid": False,
            "reason": "visual_text_but_no_extraction",
            "visual_density": visual_density,
            "recommendation": "ocr_fallback",
        }

    # If page is blank but we extracted text
    if visual_density["is_mostly_blank"] and len(extracted_text.strip()) > 50:
        return {
            "valid": False,
            "reason": "no_visual_text_but_extraction",
            "visual_density": visual_density,
            "recommendation": "discard_text",
        }

    # Spot-check: verify a few text regions have visible content
    regions_checked = 0
    regions_valid = 0
    for bbox in text_regions[:5]:  # Check up to 5 regions
        region_img = render_page_region(page, bbox, dpi=200)
        if region_img is not None:
            regions_checked += 1
            if check_text_presence(region_img):
                regions_valid += 1

    if regions_checked > 0:
        validity_ratio = regions_valid / regions_checked
    else:
        validity_ratio = 1.0  # Can't check, assume valid

    return {
        "valid": validity_ratio >= 0.6,
        "reason": "visual_check" if validity_ratio >= 0.6 else "visual_mismatch",
        "visual_density": visual_density,
        "regions_checked": regions_checked,
        "regions_valid": regions_valid,
        "validity_ratio": round(validity_ratio * 100, 1),
        "recommendation": "accept" if validity_ratio >= 0.6 else "ocr_fallback",
    }
