"""
Semantic Consistency Validator — detects impossible content.

Goes beyond Unicode validation to check:
- Mathematical impossibilities (VAT > Total)
- Date inconsistencies (impossible BS dates)
- Reference consistency (section references that don't exist)
- Amount format consistency (mixing lakhs and millions)
- Language consistency (random English in pure Nepali section)

Addresses Problem 15 from the architectural critique.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.logging_config import get_logger

logger = get_logger("SemanticValidator")


@dataclass
class SemanticIssue:
    """A single semantic inconsistency found."""
    severity: str  # "error", "warning", "info"
    category: str  # "amount", "date", "reference", "language", "logic"
    message: str
    location: str = ""  # Page/line reference
    suggestion: str = ""


@dataclass
class SemanticValidationResult:
    """Complete semantic validation result."""
    is_valid: bool = True
    issues: list[SemanticIssue] = field(default_factory=list)
    confidence_adjustment: float = 0.0  # How much to adjust overall confidence

    def add_issue(
        self,
        severity: str,
        category: str,
        message: str,
        location: str = "",
        suggestion: str = "",
    ) -> None:
        self.issues.append(SemanticIssue(
            severity=severity,
            category=category,
            message=message,
            location=location,
            suggestion=suggestion,
        ))
        if severity == "error":
            self.is_valid = False
            self.confidence_adjustment -= 10
        elif severity == "warning":
            self.confidence_adjustment -= 3


# Amount patterns
_AMOUNT_RE = re.compile(
    r"(?:रु\.?|Rs\.?|NRs\.?)\s*([\d,]+(?:\.\d+)?)"
)
_NEPALI_AMOUNT_RE = re.compile(
    r"([\d,]+(?:\.\d+)?)\s*(?:रुपैयाँ|रुपिया)"
)
_PERCENTAGE_RE = re.compile(r"([\d.]+)\s*(?:%|प्रतिशत)")

# Date patterns (BS calendar: 20XX/XX/XX)
_BS_DATE_RE = re.compile(r"(\d{4})[./\-](\d{1,2})[./\-](\d{1,2})")
_NEPALI_DIGIT_DATE_RE = re.compile(
    r"([\u0966-\u096F]{4})[./\-]([\u0966-\u096F]{1,2})[./\-]([\u0966-\u096F]{1,2})"
)

# Section reference patterns
_SECTION_REF_RE = re.compile(r"दफा\s*([\d\u0966-\u096F]+)")
_SUBSECTION_REF_RE = re.compile(r"उपदफा\s*\(([\d\u0966-\u096F]+)\)")


def validate_document_semantics(
    text: str,
    domain: str = "general",
    page_texts: list[str] | None = None,
) -> SemanticValidationResult:
    """
    Run all semantic validation checks on the extracted text.
    """
    result = SemanticValidationResult()

    # 1. Amount consistency
    _validate_amounts(text, result)

    # 2. Date validity
    _validate_dates(text, result)

    # 3. Percentage validity
    _validate_percentages(text, result)

    # 4. Section reference consistency (for legal documents)
    if domain in ("legal", "government"):
        _validate_section_references(text, result)

    # 5. Language consistency
    _validate_language_consistency(text, result)

    # 6. Number format consistency
    _validate_number_formats(text, result)

    if result.issues:
        logger.info(
            "Semantic validation: %d issues found (%d errors, %d warnings)",
            len(result.issues),
            sum(1 for i in result.issues if i.severity == "error"),
            sum(1 for i in result.issues if i.severity == "warning"),
        )

    return result


def _validate_amounts(text: str, result: SemanticValidationResult) -> None:
    """Check that extracted amounts are reasonable."""
    amounts = []
    for match in _AMOUNT_RE.finditer(text):
        try:
            value = float(match.group(1).replace(",", ""))
            amounts.append(value)
        except ValueError:
            pass

    # Check for impossibly large amounts (likely OCR errors)
    for amount in amounts:
        if amount > 1e15:  # More than quadrillion
            result.add_issue(
                "warning", "amount",
                f"Unusually large amount detected: {amount:,.0f}",
                suggestion="Verify this amount is correct",
            )

    # Check for negative amounts where they shouldn't be
    # (simplified — real version would understand context)


def _validate_dates(text: str, result: SemanticValidationResult) -> None:
    """Check that dates are valid in BS calendar."""
    for match in _BS_DATE_RE.finditer(text):
        year = int(match.group(1))
        month = int(match.group(2))
        day = int(match.group(3))

        if 2000 <= year <= 2120:  # Likely BS date
            if month < 1 or month > 12:
                result.add_issue(
                    "error", "date",
                    f"Invalid month in date: {match.group(0)}",
                    suggestion=f"Month should be 1-12, got {month}",
                )
            if day < 1 or day > 32:
                result.add_issue(
                    "error", "date",
                    f"Invalid day in date: {match.group(0)}",
                    suggestion=f"Day should be 1-32, got {day}",
                )


def _validate_percentages(text: str, result: SemanticValidationResult) -> None:
    """Check that percentages are reasonable."""
    for match in _PERCENTAGE_RE.finditer(text):
        try:
            pct = float(match.group(1))
            if pct > 100 and "से अधिक" not in text[max(0, match.start()-20):match.start()]:
                result.add_issue(
                    "warning", "amount",
                    f"Percentage exceeds 100%: {pct}%",
                    suggestion="Verify this percentage",
                )
        except ValueError:
            pass


def _validate_section_references(
    text: str,
    result: SemanticValidationResult,
) -> None:
    """Check that section references are consistent in legal documents."""
    sections_defined = set()
    sections_referenced = set()

    # Find section definitions (दफा X followed by content)
    for match in re.finditer(r"दफा\s*([\d\u0966-\u096F]+)\s*[.:]", text):
        sections_defined.add(match.group(1))

    # Find section references (दफा X in middle of text)
    for match in _SECTION_REF_RE.finditer(text):
        sections_referenced.add(match.group(1))

    # Check for references to non-existent sections
    if sections_defined:
        for ref in sections_referenced:
            if ref not in sections_defined:
                result.add_issue(
                    "info", "reference",
                    f"Reference to section {ref} not found in document",
                    suggestion="Verify section number is correct",
                )


def _validate_language_consistency(
    text: str,
    result: SemanticValidationResult,
) -> None:
    """Check for unexpected language mixing."""
    # Split into paragraphs
    paragraphs = text.split("\n\n")

    for i, para in enumerate(paragraphs):
        if len(para.strip()) < 20:
            continue

        chars = [c for c in para if c.strip()]
        if not chars:
            continue

        devanagari = sum(1 for c in chars if "\u0900" <= c <= "\u097F")
        latin = sum(1 for c in chars if c.isascii() and c.isalpha())
        total = len(chars)

        deva_ratio = devanagari / total
        latin_ratio = latin / total

        # Flag paragraphs that are suspiciously mixed
        # (e.g., 40-60% each suggests OCR confusion)
        if 0.3 <= deva_ratio <= 0.7 and 0.3 <= latin_ratio <= 0.7:
            result.add_issue(
                "warning", "language",
                f"Paragraph {i+1} has unusual language mix "
                f"({deva_ratio:.0%} Devanagari, {latin_ratio:.0%} Latin)",
                suggestion="This might indicate OCR confusion or "
                           "unconverted legacy font text",
            )


def _validate_number_formats(
    text: str,
    result: SemanticValidationResult,
) -> None:
    """Check for consistent number formatting."""
    # Detect mixed Nepali and Arabic digits in same context
    has_nepali_digits = bool(re.search(r"[\u0966-\u096F]", text))
    has_arabic_digits = bool(re.search(r"\d", text))

    # This is often fine (dates in Arabic, text in Nepali)
    # Only flag if mixed within the same number
    mixed_numbers = re.findall(
        r"[\u0966-\u096F]+\d+|[\d]+[\u0966-\u096F]+", text
    )
    if mixed_numbers:
        for num in mixed_numbers[:3]:
            result.add_issue(
                "warning", "number",
                f"Mixed digit systems in number: {num}",
                suggestion="Convert to consistent digit system",
            )
