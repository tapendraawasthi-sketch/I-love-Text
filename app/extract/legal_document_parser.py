"""
Legal document structure parser for Nepal government documents.

Recognizes and preserves the hierarchical structure of:
    - Acts (ऐन)
    - Rules (नियम / नियमावली)
    - Regulations (विनियम)
    - Circulars (परिपत्र)
    - Schedules (अनुसूची)

Structure hierarchy:
    Part (भाग)
     → Chapter (परिच्छेद)
       → Section (दफा)
         → Subsection (उपदफा)
           → Clause (खण्ड)
             → Sub-clause (उपखण्ड)
               → Item
    Schedule (अनुसूची)
    Annex
    Explanation (स्पष्टीकरण)
    Proviso (तर)
    Illustration (उदाहरण)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class LegalElementType(Enum):
    """Types of legal document elements."""
    TITLE = "title"
    PREAMBLE = "preamble"
    PART = "part"               # भाग
    CHAPTER = "chapter"         # परिच्छेद
    SECTION = "section"         # दफा
    SUBSECTION = "subsection"   # उपदफा
    CLAUSE = "clause"           # खण्ड
    SUBCLAUSE = "subclause"     # उपखण्ड
    ITEM = "item"
    SCHEDULE = "schedule"       # अनुसूची
    ANNEX = "annex"             # अनुसूची / संलग्नक
    EXPLANATION = "explanation"  # स्पष्टीकरण
    PROVISO = "proviso"         # तर
    ILLUSTRATION = "illustration"  # उदाहरण
    DEFINITION = "definition"   # परिभाषा
    TABLE = "table"
    FOOTNOTE = "footnote"
    BODY = "body"               # Regular body text


@dataclass
class LegalElement:
    """A single element in a legal document."""
    element_type: LegalElementType
    text: str
    number: str = ""            # e.g., "१", "(क)", "(1)"
    level: int = 0              # Nesting depth
    children: list[LegalElement] = field(default_factory=list)
    parent_ref: str = ""        # Reference to parent element
    page_number: int = 0


# --- Detection patterns ---

# Part: भाग-१, भाग १
_PART_RE = re.compile(
    r"^(?:भाग|PART)\s*[-–]?\s*([\u0966-\u096F\d]+)",
    re.IGNORECASE | re.MULTILINE,
)

# Chapter: परिच्छेद-१, परिच्छेद – १
_CHAPTER_RE = re.compile(
    r"^(?:परिच्छेद|Chapter)\s*[-–]?\s*([\u0966-\u096F\d]+)",
    re.IGNORECASE | re.MULTILINE,
)

# Section: दफा १, दफा १.
_SECTION_RE = re.compile(
    r"^(?:दफा|Section)\s*[-–.]?\s*([\u0966-\u096F\d]+[क-ज]?)",
    re.IGNORECASE | re.MULTILINE,
)

# Subsection: (१), (1), (क)
_SUBSECTION_RE = re.compile(
    r"^\s*\(([\u0966-\u096F\d]+[क-ज]?|[a-zA-Z])\)",
    re.MULTILINE,
)

# Clause: (क), (ख), (a), (b)
_CLAUSE_RE = re.compile(
    r"^\s*\(([क-ज]|[a-zA-Z])\)\s",
    re.MULTILINE,
)

# Schedule: अनुसूची-१
_SCHEDULE_RE = re.compile(
    r"^(?:अनुसूची|Schedule)\s*[-–]?\s*([\u0966-\u096F\d]+)?",
    re.IGNORECASE | re.MULTILINE,
)

# Explanation: स्पष्टीकरण :
_EXPLANATION_RE = re.compile(
    r"^(?:स्पष्टीकरण|Explanation)\s*[:：]?\s*",
    re.IGNORECASE | re.MULTILINE,
)

# Proviso: तर, provided that
_PROVISO_RE = re.compile(
    r"^(?:तर|Provided\s+that)\s*[:：]?\s*",
    re.IGNORECASE | re.MULTILINE,
)


def classify_legal_text(text: str) -> LegalElementType:
    """Classify a text block as a legal document element."""
    stripped = text.strip()
    if not stripped:
        return LegalElementType.BODY

    if _PART_RE.match(stripped):
        return LegalElementType.PART
    if _CHAPTER_RE.match(stripped):
        return LegalElementType.CHAPTER
    if _SECTION_RE.match(stripped):
        return LegalElementType.SECTION
    if _SCHEDULE_RE.match(stripped):
        return LegalElementType.SCHEDULE
    if _EXPLANATION_RE.match(stripped):
        return LegalElementType.EXPLANATION
    if _PROVISO_RE.match(stripped):
        return LegalElementType.PROVISO
    if _SUBSECTION_RE.match(stripped):
        return LegalElementType.SUBSECTION
    if _CLAUSE_RE.match(stripped):
        return LegalElementType.CLAUSE

    return LegalElementType.BODY


def extract_legal_number(text: str, element_type: LegalElementType) -> str:
    """Extract the number/label from a legal element."""
    patterns = {
        LegalElementType.PART: _PART_RE,
        LegalElementType.CHAPTER: _CHAPTER_RE,
        LegalElementType.SECTION: _SECTION_RE,
        LegalElementType.SUBSECTION: _SUBSECTION_RE,
        LegalElementType.CLAUSE: _CLAUSE_RE,
        LegalElementType.SCHEDULE: _SCHEDULE_RE,
    }
    pattern = patterns.get(element_type)
    if pattern:
        match = pattern.match(text.strip())
        if match and match.group(1):
            return match.group(1)
    return ""


def parse_legal_structure(
    page_texts: list[str],
) -> list[LegalElement]:
    """
    Parse extracted page texts into a legal document structure.

    Returns a flat list of LegalElements with correct types and numbering.
    """
    elements: list[LegalElement] = []

    for page_num, text in enumerate(page_texts, 1):
        # Split into paragraphs
        paragraphs = re.split(r"\n\s*\n", text)

        for para in paragraphs:
            para = para.strip()
            if not para:
                continue

            elem_type = classify_legal_text(para)
            number = extract_legal_number(para, elem_type)

            level = _type_to_level(elem_type)

            element = LegalElement(
                element_type=elem_type,
                text=para,
                number=number,
                level=level,
                page_number=page_num,
            )
            elements.append(element)

    return elements


def _type_to_level(t: LegalElementType) -> int:
    """Map legal element type to nesting level."""
    levels = {
        LegalElementType.TITLE: 0,
        LegalElementType.PREAMBLE: 0,
        LegalElementType.PART: 1,
        LegalElementType.CHAPTER: 2,
        LegalElementType.SECTION: 3,
        LegalElementType.SUBSECTION: 4,
        LegalElementType.CLAUSE: 5,
        LegalElementType.SUBCLAUSE: 6,
        LegalElementType.ITEM: 7,
        LegalElementType.SCHEDULE: 1,
        LegalElementType.EXPLANATION: 4,
        LegalElementType.PROVISO: 4,
        LegalElementType.ILLUSTRATION: 4,
        LegalElementType.TABLE: 3,
        LegalElementType.BODY: 4,
    }
    return levels.get(t, 4)


def serialize_legal_structure(elements: list[LegalElement]) -> str:
    """Serialize legal elements to readable text with hierarchy markers."""
    lines: list[str] = []

    for elem in elements:
        prefix = ""
        if elem.element_type == LegalElementType.PART:
            prefix = f"\n{'=' * 60}\nभाग {elem.number}\n{'=' * 60}\n"
        elif elem.element_type == LegalElementType.CHAPTER:
            prefix = f"\n{'—' * 40}\nपरिच्छेद {elem.number}\n{'—' * 40}\n"
        elif elem.element_type == LegalElementType.SECTION:
            prefix = f"\nदफा {elem.number}. "
        elif elem.element_type == LegalElementType.SCHEDULE:
            prefix = f"\n{'=' * 60}\nअनुसूची {elem.number}\n{'=' * 60}\n"
        elif elem.element_type == LegalElementType.EXPLANATION:
            prefix = "\n    स्पष्टीकरण : "
        elif elem.element_type == LegalElementType.PROVISO:
            prefix = "\n    तर "

        if prefix:
            lines.append(prefix + elem.text)
        else:
            lines.append(elem.text)

    return "\n".join(lines)
