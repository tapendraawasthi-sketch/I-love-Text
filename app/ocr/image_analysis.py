"""
Pre-OCR image analysis for scanned ink blocks.

Detects skew, orientation, resolution, noise, bleed-through, and shadows
before Tesseract runs. Results drive adaptive preprocessing.
"""
from __future__ import annotations

from typing import Any

import cv2
import numpy as np

_QUALITY_TIERS = ("clean", "moderate", "noisy", "poor")


def analyze_image_block(
    image_bgr: np.ndarray,
    *,
    render_dpi: int = 300,
    page_rotation: int = 0,
) -> dict[str, Any]:
    """
    Analyse a raster block before OCR.

    Returns quality metrics and a preprocessing profile.
    """
    gray = _to_gray(image_bgr)
    h, w = gray.shape[:2]
    max_dim = max(h, w)

    skew = _detect_skew(gray)
    orientation = _detect_orientation(gray, page_rotation)
    resolution = _assess_resolution(gray, render_dpi)
    noise = _assess_noise(gray)
    bleed = _detect_bleed_through(gray)
    shadows = _detect_shadows(gray)

    quality_score = _quality_score(skew, noise, bleed, shadows, resolution)
    tier = _quality_tier(quality_score)

    profile = {
        "deskew": abs(skew) >= 0.4,
        "deskew_angle": skew,
        "correct_orientation": orientation.get("needs_correction", False),
        "orientation_degrees": orientation.get("degrees", 0),
        "upscale": resolution.get("needs_upscale", False),
        "target_min_dim": resolution.get("target_min_dim", 0),
        "denoise_level": _denoise_level(noise["score"], bleed["score"]),
        "remove_shadows": shadows["detected"],
        "reduce_bleed": bleed["detected"],
        "aggressive": tier in ("noisy", "poor"),
        "digital": tier == "clean" and noise["score"] < 25,
        "quality_tier": tier,
        "quality_score": round(quality_score, 1),
    }

    return {
        "skew_degrees": round(skew, 2),
        "orientation": orientation,
        "resolution": resolution,
        "noise": noise,
        "bleed_through": bleed,
        "shadows": shadows,
        "quality_tier": tier,
        "quality_score": round(quality_score, 1),
        "render_dpi": render_dpi,
        "pixel_width": w,
        "pixel_height": h,
        "max_dimension": max_dim,
        "preprocess_profile": profile,
    }


def is_ocr_eligible_block(meta: dict[str, Any], block_type: str) -> bool:
    """
    OCR runs only on image-ink blocks.

    Excludes embedded Unicode, vector text, signatures, and QR codes.
    """
    if block_type in _OCR_NEVER_TYPES:
        return False

    if meta.get("contains_text") and not meta.get("contains_image"):
        return False

    if meta.get("encoding") in ("unicode", "legacy", "mixed") and meta.get("contains_text"):
        return False

    if meta.get("ocr_eligible"):
        return True

    if meta.get("_source") == "scanned":
        return True

    if block_type == "handwriting" and meta.get("contains_image"):
        return True

    if meta.get("contains_image") and not meta.get("contains_text"):
        return True

    return False


_OCR_NEVER_TYPES = frozenset({
    "signature", "qr_code", "barcode", "figure", "chart",
    "stamp", "formula",
})


def _to_gray(img: np.ndarray) -> np.ndarray:
    if len(img.shape) == 3:
        return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return img


def _detect_skew(gray: np.ndarray) -> float:
    inverted = cv2.bitwise_not(gray)
    coords = np.column_stack(np.where(inverted > 128))
    if len(coords) < 40:
        return 0.0

    angle = cv2.minAreaRect(coords)[-1]
    if angle < -45:
        angle = -(90 + angle)
    else:
        angle = -angle

    if abs(angle) < 0.25 or abs(angle) > 20:
        return 0.0
    return float(angle)


def _detect_orientation(gray: np.ndarray, page_rotation: int) -> dict[str, Any]:
    """Estimate whether the raster block needs rotation correction."""
    h, w = gray.shape[:2]
    aspect = w / max(h, 1)

    # Page metadata rotation
    if page_rotation in (90, 270):
        return {
            "degrees": page_rotation,
            "needs_correction": True,
            "label": "rotated_page",
        }

    # Tall narrow blocks with horizontal ink density → likely correct
    # Very wide short strips may be headers — leave as-is
    if aspect < 0.35 and h > w * 2:
        return {"degrees": 0, "needs_correction": False, "label": "portrait"}

    return {"degrees": 0, "needs_correction": False, "label": "landscape"}


def _assess_resolution(gray: np.ndarray, render_dpi: int) -> dict[str, Any]:
    h, w = gray.shape[:2]
    max_dim = max(h, w)

    # Estimate stroke height via connected components
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    n_labels, _, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
    heights = [stats[i][3] for i in range(1, n_labels) if 4 <= stats[i][3] <= max(h // 3, 20)]
    median_stroke = float(np.median(heights)) if heights else 0.0

    # Target ~20–40 px stroke height at OCR render scale
    needs_upscale = max_dim < 900 or (0 < median_stroke < 12)
    target_min = 1200 if needs_upscale else 0

    effective_dpi = render_dpi
    if median_stroke > 0:
        effective_dpi = round(render_dpi * (18 / median_stroke), 1)

    return {
        "render_dpi": render_dpi,
        "effective_dpi": effective_dpi,
        "median_stroke_px": round(median_stroke, 1),
        "max_dimension": max_dim,
        "needs_upscale": needs_upscale,
        "target_min_dim": target_min,
        "adequate": max_dim >= 900 and (median_stroke == 0 or median_stroke >= 10),
    }


def _assess_noise(gray: np.ndarray) -> dict[str, Any]:
    lap = cv2.Laplacian(gray, cv2.CV_64F)
    lap_var = float(lap.var())

    # Salt-and-pepper estimate: isolated pixels
    blurred = cv2.medianBlur(gray, 3)
    diff = np.abs(gray.astype(np.int16) - blurred.astype(np.int16))
    speckle_ratio = float(np.mean(diff > 25)) * 100

    score = min(100.0, lap_var / 8.0 + speckle_ratio * 2.5)
    return {
        "laplacian_variance": round(lap_var, 1),
        "speckle_percent": round(speckle_ratio, 2),
        "score": round(score, 1),
        "level": "high" if score > 55 else "medium" if score > 30 else "low",
    }


def _detect_bleed_through(gray: np.ndarray) -> dict[str, Any]:
    """Detect faint reverse-side text (low-contrast secondary ink)."""
    hist = cv2.calcHist([gray], [0], None, [256], [0, 256]).flatten()
    total = hist.sum() or 1
    paper_peak = float(hist[220:256].sum() / total)
    faint_peak = float(hist[160:210].sum() / total)
    ink_peak = float(hist[0:80].sum() / total)

    # Bleed: significant faint mid-tones plus primary ink
    detected = faint_peak > 0.06 and ink_peak > 0.02 and paper_peak > 0.3
    score = min(100.0, faint_peak * 200 + (30 if detected else 0))

    return {
        "detected": detected,
        "faint_tone_ratio": round(faint_peak, 3),
        "score": round(score, 1),
    }


def _detect_shadows(gray: np.ndarray) -> dict[str, Any]:
    """Detect uneven illumination / edge shadows."""
    h, w = gray.shape[:2]
    if h < 20 or w < 20:
        return {"detected": False, "score": 0.0}

    # Compare corner brightness vs centre
    margin = max(4, min(h, w) // 10)
    corners = np.concatenate([
        gray[:margin, :margin].ravel(),
        gray[:margin, -margin:].ravel(),
        gray[-margin:, :margin].ravel(),
        gray[-margin:, -margin:].ravel(),
    ])
    centre = gray[h // 4: 3 * h // 4, w // 4: 3 * w // 4]
    corner_mean = float(np.mean(corners))
    centre_mean = float(np.mean(centre))
    gradient = abs(corner_mean - centre_mean)

    # Large-scale illumination field smoothness
    bg = cv2.GaussianBlur(gray, (0, 0), sigmaX=max(h, w) / 8)
    illum_std = float(np.std(bg))

    detected = gradient > 18 or illum_std > 28
    score = min(100.0, gradient * 1.2 + illum_std * 0.8)

    return {
        "detected": detected,
        "corner_centre_delta": round(gradient, 1),
        "illumination_std": round(illum_std, 1),
        "score": round(score, 1),
    }


def _quality_score(
    skew: float,
    noise: dict[str, Any],
    bleed: dict[str, Any],
    shadows: dict[str, Any],
    resolution: dict[str, Any],
) -> float:
    score = 100.0
    score -= min(25.0, abs(skew) * 3.0)
    score -= min(30.0, noise["score"] * 0.35)
    score -= min(20.0, bleed["score"] * 0.2)
    score -= min(20.0, shadows["score"] * 0.2)
    if not resolution.get("adequate", True):
        score -= 15.0
    return max(0.0, score)


def _quality_tier(score: float) -> str:
    if score >= 80:
        return "clean"
    if score >= 60:
        return "moderate"
    if score >= 40:
        return "noisy"
    return "poor"


def _denoise_level(noise_score: float, bleed_score: float) -> str:
    combined = noise_score + bleed_score * 0.5
    if combined > 60:
        return "strong"
    if combined > 30:
        return "medium"
    return "light"
