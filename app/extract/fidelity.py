"""
Extraction fidelity modes.

forensic  — as-is accuracy: encoding conversion only when font proven;
            no dictionary / lexicon / cross-page / LLM mutations.
balanced  — NFC + proven conversion; light structural cleanup only.
assisted  — optional knowledge-base / lexicon / cross-page repairs (opt-in).
ocr_max   — scan OCR path; PUA cleanup only, no lexicon swaps.
"""
from __future__ import annotations

from contextvars import ContextVar
from typing import Literal

FidelityMode = Literal["forensic", "balanced", "assisted", "ocr_max"]

VALID_FIDELITY: tuple[FidelityMode, ...] = ("forensic", "balanced", "assisted", "ocr_max")

_current_fidelity: ContextVar[FidelityMode] = ContextVar("extract_fidelity", default="forensic")


def normalize_fidelity(value: str | None) -> FidelityMode:
    if not value:
        return "forensic"
    v = value.strip().lower()
    if v in VALID_FIDELITY:
        return v  # type: ignore[return-value]
    # Back-compat aliases
    if v in ("as_is", "as-is", "exact", "raw"):
        return "forensic"
    if v in ("clean", "cleaned", "default"):
        return "balanced"
    if v in ("repair", "smart", "ai"):
        return "assisted"
    raise ValueError(
        f"Invalid fidelity: {value}. Use: {', '.join(VALID_FIDELITY)}"
    )


def get_fidelity() -> FidelityMode:
    return _current_fidelity.get()


def set_fidelity(mode: FidelityMode):
    """Set fidelity for the current context; returns a reset token."""
    return _current_fidelity.set(mode)


def reset_fidelity(token) -> None:
    _current_fidelity.reset(token)


def allow_mutations(mode: FidelityMode | None = None) -> bool:
    """True when knowledge-base / lexicon / cross-page edits are allowed."""
    m = mode or get_fidelity()
    return m == "assisted"


def allow_lexicon_repair(mode: FidelityMode | None = None) -> bool:
    """Lexicon fuzzy word swaps — assisted only."""
    return allow_mutations(mode)


def allow_cross_page_corrections(mode: FidelityMode | None = None) -> bool:
    return allow_mutations(mode)


def allow_unicode_matra_repair(mode: FidelityMode | None = None) -> bool:
    """Matra redistribution — allowed in balanced/assisted, not forensic."""
    m = mode or get_fidelity()
    return m in ("balanced", "assisted", "ocr_max")


def allow_placeholders(mode: FidelityMode | None = None) -> bool:
    """[Signature]/[Figure] placeholders — off in forensic."""
    m = mode or get_fidelity()
    return m != "forensic"


def min_corruption_for_repair(mode: FidelityMode | None = None) -> float:
    """Minimum corruption_score before any OCR post-repair runs."""
    m = mode or get_fidelity()
    if m == "assisted":
        return 0.05
    if m in ("balanced", "ocr_max"):
        return 0.15
    return 1.1  # forensic: effectively never lexicon-repair
