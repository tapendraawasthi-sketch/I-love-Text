"""LLM integration module for Nepali language understanding and OCR enhancement."""

from .nepali_language_model import (
    NepaliLanguageModel,
    get_nepali_language_model,
    correct_ocr_text,
)

__all__ = [
    "NepaliLanguageModel",
    "get_nepali_language_model",
    "correct_ocr_text",
]
