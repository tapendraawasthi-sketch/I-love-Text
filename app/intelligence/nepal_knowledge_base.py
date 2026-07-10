"""
Nepal Knowledge Base — comprehensive vocabulary for domain-aware correction.

Contains:
- Legal terminology (Acts, Courts, Government)
- Accounting/Financial terminology
- Banking terminology
- Tax terminology
- Government structure terminology
- Common Nepali words with high frequency in documents

Used for:
- OCR post-correction (replace OCR errors with known words)
- Confidence scoring (known words get higher confidence)
- Semantic validation (impossible combinations flagged)
- Domain detection

Addresses Problems 9, 10, 11 from the architectural critique.
"""
from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

from app.logging_config import get_logger

logger = get_logger("NepalKnowledgeBase")


# =============================================================================
# LEGAL VOCABULARY
# =============================================================================
LEGAL_TERMS: set[str] = {
    # Courts and Legal System
    "अदालत", "सर्वोच्च", "उच्च", "जिल्ला", "न्यायालय", "न्यायाधीश",
    "न्यायपालिका", "वकील", "अभिवक्ता", "प्रतिवादी", "वादी",
    "मुद्दा", "फैसला", "आदेश", "निर्णय", "पुनरावेदन",

    # Legal Concepts
    "कानून", "ऐन", "नियमावली", "विनियमावली", "संविधान", "अध्यादेश",
    "दफा", "उपदफा", "खण्ड", "अनुसूची", "परिच्छेद", "भाग",
    "प्रकरण", "व्याख्या", "परन्तुक", "स्पष्टीकरण",

    # Legal Actions
    "अभियोग", "प्रमाण", "गवाही", "साक्षी", "बयान",
    "जाँच", "अनुसन्धान", "तहकिकात", "पक्राउ", "गिरफ्तारी",
    "जमानत", "धरौटी", "थुनामा", "कैद", "जरिवाना",

    # Legal Documents
    "लिखत", "सम्झौता", "अनुबन्ध", "करार", "करारनामा",
    "अधिकारपत्र", "प्रमाणपत्र", "विश्वासपत्र",

    # Government Bodies
    "नेपाल", "सरकार", "मन्त्रालय", "विभाग", "कार्यालय",
    "आयोग", "समिति", "निकाय", "प्रदेश", "स्थानीय",
    "पालिका", "गाउँपालिका", "नगरपालिका", "महानगरपालिका",
    "संसद", "प्रतिनिधिसभा", "राष्ट्रियसभा",

    # Legal Principles
    "अधिकार", "कर्तव्य", "दायित्व", "जिम्मेवारी", "जवाफदेही",
    "हक", "अपराध", "दण्ड", "सजाय", "क्षतिपूर्ति",
    "मुआब्जा", "हर्जाना", "रकम", "वैधानिकता",
}

# =============================================================================
# ACCOUNTING / FINANCIAL VOCABULARY
# =============================================================================
ACCOUNTING_TERMS: set[str] = {
    # Core Accounting
    "खाता", "जर्नल", "खातावही", "खाताबही", "बही",
    "नामेसी", "जम्मा", "शेष", "बाँकी", "रकम",
    "नगद", "चेक", "ड्राफ्ट", "भौचर",

    # Financial Statements
    "वासलात", "नाफानोक्सान", "आर्थिक", "विवरण",
    "तुलनपत्र", "नगदप्रवाह", "लेखापरीक्षण",

    # Assets
    "सम्पत्ति", "चलसम्पत्ति", "अचलसम्पत्ति", "स्थिरसम्पत्ति",
    "पूँजीगत", "हिसाब", "मौज्दात",

    # Liabilities
    "दायित्व", "चालू", "दीर्घकालीन", "ऋण", "कर्जा",
    "देय", "भुक्तानी", "तिर्नुपर्ने",

    # Revenue/Expenses
    "आम्दानी", "आय", "खर्च", "व्यय", "नाफा", "नोक्सान",
    "राजस्व", "प्राप्ति", "बिक्री", "खरीद",

    # Capital
    "पूँजी", "सेयर", "शेयर", "लाभांश", "सञ्चित",
    "कोष", "जगेडा", "सुरक्षित",

    # Tax Related
    "कर", "करयोग्य", "भ्याट", "मूल्य", "अभिवृद्धि",
    "आयकर", "अग्रिम", "कर्तव्य", "छुट",

    # Numbers/Amounts
    "रुपैयाँ", "पैसा", "हजार", "लाख", "करोड", "अर्ब",
    "प्रतिशत",

    # Periods
    "आर्थिक", "वर्ष", "चैत्र", "बैशाख", "जेठ", "असार",
    "श्रावण", "भदौ", "असोज", "कात्तिक", "मंसिर", "पौष",
    "माघ", "फाल्गुन",
}

# =============================================================================
# BANKING VOCABULARY
# =============================================================================
BANKING_TERMS: set[str] = {
    "बैंक", "बैंकिङ", "निक्षेप", "बचत", "चल्ती",
    "स्थिर", "मुद्दती", "ब्याज", "ब्याजदर",
    "सापटी", "ऋणपत्र", "धितोपत्र", "प्रतिभूति",
    "विनिमय", "हुण्डी", "पत्र", "प्रत्याभूति",
    "लगानी", "कारोबार", "भुक्तानी", "निकासी",
    "जम्मा", "खाता", "शाखा", "केन्द्रीय",
}

# =============================================================================
# COMMON NEPALI WORDS (high-frequency in documents)
# =============================================================================
COMMON_NEPALI_WORDS: set[str] = {
    # Conjunctions and particles
    "र", "को", "का", "की", "ले", "मा", "बाट", "सम्म",
    "लाई", "प्रति", "सँग", "देखि", "भन्दा", "पनि",
    "तर", "वा", "अथवा", "तथा", "एवं", "यो", "त्यो",
    "यी", "ती", "कुनै", "सबै", "प्रत्येक",

    # Verbs (common forms)
    "गर्नु", "हुनु", "भएको", "गरेको", "गरिएको",
    "बनाउनु", "दिनु", "लिनु", "राख्नु", "हेर्नु",
    "गर्न", "हुन", "भए", "गरे", "गरि",

    # Common nouns
    "व्यक्ति", "संस्था", "कम्पनी", "कर्मचारी", "अधिकारी",
    "निर्देशक", "सदस्य", "अध्यक्ष", "सचिव", "प्रमुख",
    "जनता", "नागरिक", "राष्ट्र", "देश", "प्रदेश",

    # Numbers
    "एक", "दुई", "तीन", "चार", "पाँच", "छ", "सात",
    "आठ", "नौ", "दश", "सय", "हजार", "लाख",

    # Time
    "दिन", "हप्ता", "महिना", "वर्ष", "साल", "मिति",
    "समय", "अवधि", "अन्तर्गत", "भित्र", "पछि",

    # Adjectives
    "नयाँ", "पुरानो", "ठूलो", "सानो", "पहिलो", "अन्तिम",
    "मुख्य", "विशेष", "सामान्य", "आवश्यक",
}

# =============================================================================
# COMBINED VOCABULARY
# =============================================================================
ALL_KNOWN_WORDS: set[str] = (
    LEGAL_TERMS | ACCOUNTING_TERMS | BANKING_TERMS | COMMON_NEPALI_WORDS
)


# =============================================================================
# CONSONANT SKELETON INDEX for fuzzy matching
# =============================================================================
def _consonant_skeleton(word: str) -> str:
    """Extract consonant skeleton for fuzzy matching."""
    return "".join(
        c for c in word
        if 0x0915 <= ord(c) <= 0x0939  # Devanagari consonants
    )


@lru_cache(maxsize=1)
def _build_skeleton_index() -> dict[str, list[str]]:
    """Build consonant skeleton → word list index."""
    index: dict[str, list[str]] = {}
    for word in ALL_KNOWN_WORDS:
        sk = _consonant_skeleton(word)
        if sk:
            index.setdefault(sk, []).append(word)
    return index


def get_skeleton_index() -> dict[str, list[str]]:
    return _build_skeleton_index()


# =============================================================================
# WORD LOOKUP AND CORRECTION
# =============================================================================

def is_known_word(word: str) -> bool:
    """Check if a word is in the knowledge base."""
    return word in ALL_KNOWN_WORDS


def get_domain_for_word(word: str) -> str | None:
    """Determine which domain a word belongs to."""
    if word in LEGAL_TERMS:
        return "legal"
    if word in ACCOUNTING_TERMS:
        return "accounting"
    if word in BANKING_TERMS:
        return "banking"
    if word in COMMON_NEPALI_WORDS:
        return "common"
    return None


def find_closest_word(
    word: str,
    max_distance: int = 2,
    domain: str | None = None,
) -> list[tuple[str, int, str]]:
    """
    Find closest known words to a potentially misspelled word.

    Returns list of (word, edit_distance, domain) sorted by distance.
    """
    skeleton = _consonant_skeleton(word)
    index = get_skeleton_index()

    candidates: list[tuple[str, int, str]] = []

    # First try exact skeleton match
    if skeleton in index:
        for candidate in index[skeleton]:
            dist = _levenshtein(word, candidate)
            if dist <= max_distance:
                d = get_domain_for_word(candidate) or "unknown"
                if domain is None or d == domain:
                    candidates.append((candidate, dist, d))

    # Also try nearby skeletons (1 char difference)
    if not candidates and len(skeleton) >= 2:
        for sk, words in index.items():
            if abs(len(sk) - len(skeleton)) <= 1:
                sk_dist = _levenshtein(skeleton, sk)
                if sk_dist <= 1:
                    for candidate in words:
                        dist = _levenshtein(word, candidate)
                        if dist <= max_distance:
                            d = get_domain_for_word(candidate) or "unknown"
                            candidates.append((candidate, dist, d))

    candidates.sort(key=lambda x: x[1])
    return candidates[:5]


def correct_word(word: str, domain: str | None = None) -> tuple[str, float, str]:
    """
    Attempt to correct a word using the knowledge base.

    Returns: (corrected_word, confidence, source)
    """
    # Exact match
    if is_known_word(word):
        return word, 100.0, "exact_match"

    # Fuzzy match
    matches = find_closest_word(word, max_distance=2, domain=domain)
    if matches:
        best_word, best_dist, best_domain = matches[0]
        if best_dist == 0:
            return best_word, 100.0, f"exact_{best_domain}"
        elif best_dist == 1:
            return best_word, 85.0, f"near_match_{best_domain}"
        elif best_dist == 2:
            return best_word, 65.0, f"fuzzy_match_{best_domain}"

    return word, 30.0, "unknown"


def _levenshtein(a: str, b: str) -> int:
    """Compute Levenshtein edit distance."""
    if not a:
        return len(b)
    if not b:
        return len(a)
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i]
        for j, cb in enumerate(b, 1):
            curr.append(min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = curr
    return prev[-1]


# =============================================================================
# SEMANTIC VALIDATION
# =============================================================================

def validate_amount_consistency(amounts: list[tuple[str, float]]) -> list[str]:
    """
    Validate that extracted amounts are semantically consistent.

    Example: VAT cannot exceed total amount.
    """
    issues = []

    # Find labeled amounts
    labels = {label.lower(): value for label, value in amounts}

    # Check: VAT should be 13% of taxable amount (Nepal standard rate)
    if "भ्याट" in labels and "करयोग्य" in labels:
        expected_vat = labels["करयोग्य"] * 0.13
        actual_vat = labels["भ्याट"]
        if abs(actual_vat - expected_vat) > expected_vat * 0.05:
            issues.append(
                f"VAT mismatch: expected ~{expected_vat:.2f}, got {actual_vat:.2f}"
            )

    # Check: Total should be >= any component
    if "जम्मा" in labels:
        total = labels["जम्मा"]
        for label, value in amounts:
            if label.lower() != "जम्मा" and value > total * 1.01:
                issues.append(
                    f"Component '{label}' ({value}) exceeds total ({total})"
                )

    return issues


def validate_date_format(text: str) -> list[str]:
    """Check for valid Nepali date formats (BS calendar)."""
    issues = []

    # BS year should be 2050-2100 range typically
    year_pattern = re.compile(r"(\d{4})[./\-](\d{1,2})[./\-](\d{1,2})")
    for match in year_pattern.finditer(text):
        year = int(match.group(1))
        month = int(match.group(2))
        day = int(match.group(3))

        if year < 2000 or year > 2120:
            issues.append(f"Unlikely BS year: {year}")
        if month < 1 or month > 12:
            issues.append(f"Invalid month: {month}")
        if day < 1 or day > 32:
            issues.append(f"Invalid day: {day}")

    return issues
