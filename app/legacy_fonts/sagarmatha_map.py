"""
Sagarmatha font to Unicode Devanagari mapping.

Sagarmatha is a legacy ASCII-encoded Nepali font.  It uses a different key
layout from Preeti.  This built-in map is used when npttf2utf is unavailable.

Key reference: Sagarmatha encoding (standard Nepal government document encoding).
"""
import re

# Sagarmatha → Unicode mapping.
# Multi-char sequences listed FIRST so the longest-match converter hits them.
SAGARMATHA_MAP: dict[str, str] = {
    # ── Multi-char vowel + matra combinations (longest first) ──
    "cf}": "औ", "cf]": "ओ", "cf": "आ",
    "O{": "ई", "P]": "ऐ", "pm": "ऊ",

    # ── Standalone vowels ──
    "c": "अ", "O": "इ", "p": "उ", "P": "ए", "C": "ऋ",

    # ── Consonants ──
    "s": "क", "v": "ख", "u": "ग", "3": "घ", "ä": "ङ",
    "r": "च", "5": "छ", "h": "ज", "Ü": "झ", "`": "ञ",
    "6": "ट", "7": "ठ", "8": "ड", "9": "ढ", "0": "ण",
    "t": "त", "y": "थ", "b": "द", "w": "ध", "g": "न",
    "k": "प", "m": "फ", "a": "ब", "e": "भ", "d": "म",
    "o": "य", "/": "र", "n": "ल", "j": "व",
    "z": "श", "S": "ष", ";": "स", "x": "ह",
    "I": "क्ष", "q": "त्र", "1": "ज्ञ",

    # ── Matras ──
    "f]": "ो", "f}": "ौ", "f": "ा",
    "L": "ी", "l": "ि", '"': "ु", '""': "ू",
    "]": "े", "}": "ै", "[": "ृ", "\\": "्",

    # ── Anusvara, Chandrabindu, Visarga ──
    "+": "ं", "F": "ँ", "M": "ः",

    # ── Nepali digits ──
    ")": "०", "!": "१", "@": "२", "#": "३", "$": "४",
    "%": "५", "^": "६", "&": "७", "*": "८", "(": "९",

    # ── Punctuation ──
    ".": "।", "..": "॥",

    # ── Common conjuncts ──
    "Q/": "त्र", "Qo": "त्य", "Qd": "त्म", "Qj": "त्व", "Q": "त्",
    "K/": "प्र", "Ko": "प्य", "Kn": "प्ल", "K\\": "प्",
    "B/": "द्र", "Bf": "द्य", "B": "द्",
    "J/": "श्र", "Jo": "श्य", "J": "श्",
    "x\\": "ह्", "X/": "ह्र", "Xo": "ह्य",
    "G/": "न्र", "Go": "न्य", "Gb": "न्द", "Gw": "न्ध",
    "¥": "र्",
}


def sagarmatha_to_unicode(text: str) -> str:
    """Convert Sagarmatha-encoded ASCII to Unicode Devanagari (longest-match)."""
    if not text:
        return text
    sorted_keys = sorted(SAGARMATHA_MAP.keys(), key=len, reverse=True)
    result = []
    i = 0
    while i < len(text):
        matched = False
        for key in sorted_keys:
            if text[i:i + len(key)] == key:
                result.append(SAGARMATHA_MAP[key])
                i += len(key)
                matched = True
                break
        if not matched:
            result.append(text[i])
            i += 1
    return "".join(result)


def is_likely_sagarmatha(text: str) -> bool:
    """Return True when text looks like Sagarmatha-encoded input."""
    if not text or not text.strip():
        return False
    signals = re.compile(r"[;/\\lLfmosuv]")
    ascii_letters = sum(1 for c in text if c.isascii() and c.isalpha())
    if ascii_letters == 0:
        return False
    return len(signals.findall(text)) / max(ascii_letters, 1) > 0.18


if __name__ == "__main__":
    tests = [("g]kfn", "नेपाल"), ("sf7df8f}+", "काठमाडौं")]
    for src, exp in tests:
        got = sagarmatha_to_unicode(src)
        print(f"{src!r} → {got!r}: {'PASS' if got == exp else 'CHECK'}")
