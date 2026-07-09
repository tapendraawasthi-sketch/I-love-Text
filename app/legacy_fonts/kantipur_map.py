"""
Kantipur / e-Kantipur font to Unicode Devanagari mapping.

Kantipur is the font used by Kantipur Publications (major Nepali newspaper).
It has a slightly different layout from Preeti, especially for matras and
conjunct consonants.
"""
import re

KANTIPUR_MAP: dict[str, str] = {
    # ── Multi-char combinations (longest first) ──
    "cf}": "औ", "cf]": "ओ", "cf": "आ",
    "O{": "ई",

    # ── Standalone vowels ──
    "c": "अ", "O": "इ", "p": "उ", "P": "ए", "C": "ऋ",

    # ── Consonants (Kantipur layout) ──
    "s": "क", "v": "ख", "u": "ग", "3": "घ",
    "r": "च", "5": "छ", "h": "ज", "`": "ञ",
    "6": "ट", "7": "ठ", "8": "ड", "9": "ढ", "0": "ण",
    "t": "त", "y": "थ", "b": "द", "w": "ध", "g": "न",
    "k": "प", "m": "फ", "a": "ब", "e": "भ", "d": "म",
    "o": "य", "/": "र", "n": "ल", "j": "व",
    "z": "श", ";": "स", "x": "ह",
    "I": "क्ष", "q": "त्र", "1": "ज्ञ",

    # ── Matras ──
    "f]": "ो", "f}": "ौ",
    "f": "ा", "L": "ी", "l": "ि",
    '"': "ु", '""': "ू",
    "]": "े", "}": "ै",
    "[": "ृ", "\\": "्",

    # ── Anusvara, Chandrabindu, Visarga ──
    "+": "ं", "F": "ँ", "M": "ः",

    # ── Nepali digits ──
    ")": "०", "!": "१", "@": "२", "#": "३", "$": "४",
    "%": "५", "^": "६", "&": "७", "*": "८", "(": "९",

    # ── Punctuation ──
    ".": "।",

    # ── Common conjuncts ──
    "Q/": "त्र", "Qo": "त्य", "Q": "त्",
    "K/": "प्र", "Ko": "प्य", "K\\": "प्",
    "B/": "द्र", "Bf": "द्य", "B": "द्",
    "¥": "र्",
    "G/": "न्र", "Go": "न्य",
    "J/": "श्र", "J": "श्",
}


def kantipur_to_unicode(text: str) -> str:
    """Convert Kantipur-encoded ASCII to Unicode Devanagari (longest-match)."""
    if not text:
        return text
    sorted_keys = sorted(KANTIPUR_MAP.keys(), key=len, reverse=True)
    result = []
    i = 0
    while i < len(text):
        matched = False
        for key in sorted_keys:
            if text[i:i + len(key)] == key:
                result.append(KANTIPUR_MAP[key])
                i += len(key)
                matched = True
                break
        if not matched:
            result.append(text[i])
            i += 1
    return "".join(result)


if __name__ == "__main__":
    sample = "g]kfn"
    print(f"Kantipur sample: {kantipur_to_unicode(sample)!r}")
