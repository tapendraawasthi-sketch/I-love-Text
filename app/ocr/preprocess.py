"""
Image preprocessing tuned for Nepali OCR accuracy and speed.
"""
from __future__ import annotations

import cv2
import numpy as np

from app.config import MAX_OCR_DIMENSION, MIN_OCR_DIMENSION


def load_image_bytes(data: bytes) -> np.ndarray:
    """Decode uploaded bytes to BGR numpy array."""
    np_arr = np.frombuffer(data, np.uint8)
    img = cv2.imdecode(np_arr, cv2.IMREAD_UNCHANGED)
    if img is None:
        raise ValueError("Failed to decode image bytes.")

    if len(img.shape) == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    elif len(img.shape) == 3 and img.shape[2] == 4:
        bgr = img[:, :, :3]
        alpha = img[:, :, 3].astype(np.float64) / 255.0
        bg = np.ones_like(bgr, dtype=np.float64) * 255.0
        img = bgr * alpha[..., None] + bg * (1.0 - alpha[..., None])
        img = np.clip(img, 0, 255).astype(np.uint8)

    return img


def _to_grayscale(img: np.ndarray) -> np.ndarray:
    if len(img.shape) == 3:
        return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return img


def _normalize_resolution(img: np.ndarray, *, digital: bool) -> np.ndarray:
    """Upscale small images for accuracy and downscale huge pages for speed."""
    h, w = img.shape[:2]
    max_dim = max(h, w)
    target_min = MIN_OCR_DIMENSION if digital else MIN_OCR_DIMENSION - 200
    target_max = MAX_OCR_DIMENSION

    if max_dim > target_max:
        scale = target_max / max_dim
        new_w, new_h = int(w * scale), int(h * scale)
        return cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_AREA)

    if max_dim < target_min:
        scale = min(target_min / max_dim, 3.0)
        new_w, new_h = int(w * scale), int(h * scale)
        return cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_CUBIC)

    return img


def _denoise_fast(img: np.ndarray) -> np.ndarray:
    return cv2.bilateralFilter(img, d=7, sigmaColor=55, sigmaSpace=55)


def _denoise_strong(img: np.ndarray) -> np.ndarray:
    return cv2.fastNlMeansDenoising(
        img, None, h=12.0, templateWindowSize=7, searchWindowSize=21
    )


def _apply_clahe(img: np.ndarray, clip_limit: float = 2.0) -> np.ndarray:
    clahe = cv2.createCLAHE(clipLimit=clip_limit, tileGridSize=(8, 8))
    return clahe.apply(img)


def _sharpen(img: np.ndarray) -> np.ndarray:
    blurred = cv2.GaussianBlur(img, (0, 0), sigmaX=1.0)
    return cv2.addWeighted(img, 1.6, blurred, -0.6, 0)


def _emphasize_shirorekha(img: np.ndarray) -> np.ndarray:
    """Boost horizontal strokes used by Devanagari top lines."""
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 1))
    horizontal = cv2.morphologyEx(img, cv2.MORPH_CLOSE, kernel, iterations=1)
    return cv2.addWeighted(img, 0.82, horizontal, 0.18, 0)


def _binarize(img: np.ndarray) -> np.ndarray:
    _, th_otsu = cv2.threshold(img, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    th_adapt = cv2.adaptiveThreshold(
        img, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 15, 5
    )
    local_std = np.std(cv2.GaussianBlur(img, (25, 25), 0).astype(np.float64))
    return th_adapt if local_std > 20 else th_otsu


def _deskew(img: np.ndarray) -> np.ndarray:
    inverted = cv2.bitwise_not(img)
    coords = np.column_stack(np.where(inverted > 0))
    if len(coords) < 50:
        return img

    angle = cv2.minAreaRect(coords)[-1]
    if angle < -45:
        angle = -(90 + angle)
    else:
        angle = -angle

    if abs(angle) < 0.3 or abs(angle) > 15:
        return img

    h, w = img.shape[:2]
    center = (w // 2, h // 2)
    matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
    return cv2.warpAffine(
        img,
        matrix,
        (w, h),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=255,
    )


def _morphological_clean(img: np.ndarray) -> np.ndarray:
    kernel = np.ones((2, 2), np.uint8)
    inverted = cv2.bitwise_not(img)
    opened = cv2.morphologyEx(inverted, cv2.MORPH_OPEN, kernel)
    return cv2.bitwise_not(opened)


def _digital_enhance(img: np.ndarray) -> np.ndarray:
    """Enhance rendered PDF/DOCX pages while preserving Devanagari marks."""
    img = _apply_clahe(img, clip_limit=2.6)
    img = _sharpen(img)
    img = _emphasize_shirorekha(img)

    if float(np.std(img)) < 42.0:
        _, img = cv2.threshold(img, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)

    return img


def preprocess_for_ocr(
    image_bgr: np.ndarray,
    *,
    aggressive: bool = False,
    digital: bool = False,
) -> np.ndarray:
    """
    Preprocessing pipeline optimized for Nepali OCR.

    Digital pages use a fast grayscale enhancement path.
    Scans use denoise + binarize, with stronger cleanup only on retry.
    """
    img = _to_grayscale(image_bgr)
    img = _normalize_resolution(img, digital=digital)

    if digital:
        return _digital_enhance(img)

    img = _denoise_strong(img) if aggressive else _denoise_fast(img)
    img = _apply_clahe(img)
    img = _binarize(img)
    img = _deskew(img)

    if aggressive:
        img = _morphological_clean(img)

    return img
