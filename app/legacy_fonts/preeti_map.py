"""
Complete Preeti to Unicode Devanagari conversion mapping.

This is a built-in fallback that works without external dependencies.
Based on the standard Preeti font encoding used in Nepal.
"""
import re

# Preeti to Unicode character mapping (comprehensive)
PREETI_MAP = {
    # Multi-char vowels (must come first)
    "cf}": "औ", "cf]": "ओ", "cf": "आ",
    "O{": "ई", "P]": "ऐ", "pm": "ऊ",
    
    # Single vowels
    "c": "अ", "O": "इ", "p": "उ", "P": "ए", "C": "ऋ",
    
    # Consonants
    "s": "क", "v": "ख", "u": "ग", "3": "घ", "ª": "ङ",
    "r": "च", "5": "छ", "h": "ज", "‰": "झ", "`": "ञ",
    "6": "ट", "7": "ठ", "8": "ड", "9": "ढ", "0": "ण",
    "t": "त", "y": "थ", "b": "द", "w": "ध", "g": "न",
    "k": "प", "m": "फ", "a": "ब", "e": "भ", "d": "म",
    "o": "य", "/": "र", "n": "ल", "j": "व",
    "z": "श", "if": "ष", ";": "स", "x": "ह",
    "I": "क्ष", "q": "त्र", "1": "ज्ञ",
    
    # Multi-char matras (must come before single char)
    "f]": "ो", "f}": "ौ", "\"\"": "ू",
    
    # Single char matras
    "f": "ा", "L": "ी", "l": "ि", 
    "\"": "ु",
    "]": "े", "}": "ै",
    "[": "ृ", "\\": "्",
    
    # Anusvara, Chandrabindu, Visarga
    "+": "ं", "F": "ँ", "M": "ः",
    
    # Numbers
    ")": "०", "!": "१", "@": "२", "#": "३", "$": "४",
    "%": "५", "^": "६", "&": "७", "*": "८", "(": "९",
    
    # Punctuation and special
    ".": "।", "Ù": "॥", "Ã": "ः",
    
    # Common conjuncts and combinations
    "Qm": "क्र", "S": "क्क", "Ss": "क्क", "Sof": "क्या", "Sn": "क्ल",
    "Sv": "क्व", "Iff": "क्षा", "If": "क्ष",
    "Vu": "ख्य", "Un": "ग्ल", "Uo": "ग्य", "Uw": "ग्ध", "U/": "ग्र",
    "£": "घ्", "ª\\": "ङ्",
    "Ro": "च्य", "R5": "च्छ",
    "Hd": "ज्म", "Ho": "ज्य", "H/": "ज्र", "H`": "ज्ञ", "‚": "झ्",
    "¡": "ट्ट", "¢": "ट्ठ", "£": "ड्ड", "¤": "ड्ढ",
    "Q": "त्", "Q\\": "त्", "Qo": "त्य", "Q/": "त्र", "Qd": "त्म", "Qj": "त्व",
    "Yf": "थ्", "Yo": "थ्य", "Y/": "थ्र",
    "4": "द्द", "2": "द्ध", "å": "द्व", "Bf": "द्य", "B": "द्", "B/": "द्र", "Bdf": "द्मा",
    "W": "ध्", "Wo": "ध्य", "W/": "ध्र",
    "Gx": "न्ह", "Gt": "न्त", "Gy": "न्थ", "Gb": "न्द", "Gw": "न्ध", "Gg": "न्न",
    "Go": "न्य", "G/": "न्र",
    "Kof": "प्या", "Ko": "प्य", "K/": "प्र", "Kn": "प्ल", "Kk": "प्प", "K\\": "प्",
    "km": "फ्", "Dof": "ब्या", "Do": "ब्य", "D/": "ब्र", "Da": "ब्ब", "D": "ब्",
    "Eo": "भ्य", "E/": "भ्र", "Ed": "भ्म",
    "do": "म्य", "Dn": "म्ल", "Dd": "म्म", "d/": "म्र",
    "N": "ल्", "Nn": "ल्ल", "No": "ल्य",
    "J": "श्", "Jo": "श्य", "Jj": "श्व", "J/": "श्र",
    "i": "ष्", "i6": "ष्ट", "i7": "ष्ठ",
    ";\\": "स्", ";g": "स्न", ";d": "स्म", ";t": "स्त", ";y": "स्थ", ";k": "स्प",
    ";j": "स्व", ";/": "स्र", "Xo": "स्य",
    "x\\": "ह्", "Xd": "ह्म", "Xo": "ह्य", "X/": "ह्र", "Xn": "ह्ल",
    "If\\": "क्ष्", "If/": "क्षर", "Ifo": "क्ष्य",
    "qo": "त्र्य", "q/": "त्रर",
    "1o": "ज्ञ्य", "1f": "ज्ञा",
    
    # More conjuncts
    "¥": "र्",  # Repha (र् before consonant)
    "ß": "द्व", 
    "®": "र्",
    "§": "द्ध",
    "¨": "ड्ढ",
    "©": "श्र",
    "ª": "ङ",
    "«": "ट्र",
    "¬": "ड्र",
    "­": "ढ्य",
    "®": "र्",
    "°": "ट्ट",
    "±": "ठ्ठ",
    "²": "ड्ड",
    "³": "ड्ढ",
    "´": "ण्ट",
    "µ": "ण्ठ",
    "¶": "ण्ड",
    "·": "ण्ढ",
    "¸": "ण्ण",
    "¹": "त्त",
    "º": "त्र",
    "»": "द्ग",
    "¼": "द्घ",
    "½": "द्ब",
    "¾": "द्भ",
    "¿": "द्म",
    "À": "द्य",
    "Â": "द्र",
    "Ä": "ट्य",
    "Å": "ठ्य",
    "Æ": "ड्य",
    "Ç": "ण्य",
    "È": "प्त",
    "É": "श्च",
    "Ê": "श्न",
    "Ë": "श्व",
    "Ì": "स्क",
    "Í": "स्ख",
    "Î": "स्त",
    "Ï": "स्थ",
    "Ð": "स्प",
    "Ñ": "स्फ",
    "Ò": "स्य",
    "Ó": "स्र",
    "Ô": "स्ल",
    "Õ": "स्व",
    "Ö": "ह्न",
    "×": "ह्म",
    "Ø": "ह्य",
    "Ù": "ह्र",
    "Ú": "ह्ल",
    "Û": "ह्व",
    "Ý": "द्द",
    "Þ": "क्त",
    "à": "ट्ट",
    "á": "ट्ठ",
    "â": "ड्ड",
    "ã": "ड्ढ",
    "ä": "ठ्ठ",
    "å": "द्व",
    "æ": "द्य",
    "ç": "द्द",
    "è": "द्ध",
    "é": "द्न",
    "ê": "द्ब",
    "ë": "द्भ",
    "ì": "द्म",
    "í": "द्य",
    "ò": "न्त",
    "ó": "न्द",
    "ô": "न्ध",
    "ö": "न्न",
    "ø": "ल्ल",
    "ù": "श्च",
    "ú": "क्ष",
    "É": "ट्ट",
    "Ê": "ट्ठ",
    "Ë": "ड्ड",
    "Ì": "ड्ढ",
    "Í": "ढ्ढ",
    "Î": "ण्ण",
    "×": "।",
    "Ø": "॥",
    "·": "ˈ",
    "–": "–",
    "—": "—",
}

# Extended character codes for special Preeti symbols
PREETI_EXTENDED = {
    chr(i): PREETI_MAP.get(chr(i), chr(i))
    for i in range(128, 256)
    if chr(i) in PREETI_MAP
}


def _build_sorted_keys():
    """Build keys sorted by length (longest first) for proper replacement."""
    all_keys = list(PREETI_MAP.keys())
    return sorted(all_keys, key=len, reverse=True)


_SORTED_KEYS = _build_sorted_keys()


def preeti_to_unicode(text: str) -> str:
    """
    Convert Preeti-encoded ASCII text to Unicode Devanagari.

    Uses longest-match-first so multi-char sequences like "cf}" are
    matched before their component characters "c", "f", "}".
    """
    if not text:
        return text

    # Sort keys longest-first so greedy matching works correctly.
    sorted_keys = sorted(PREETI_MAP.keys(), key=len, reverse=True)

    result = []
    i = 0
    while i < len(text):
        matched = False
        for key in sorted_keys:
            if text[i:i + len(key)] == key:
                result.append(PREETI_MAP[key])
                i += len(key)
                matched = True
                break
        if not matched:
            result.append(text[i])
            i += 1
    return "".join(result)


def _fix_preeti_output(text: str) -> str:
    """Fix common issues in Preeti conversion output."""
    # Fix 'i' matra placement (should come after consonant)
    text = re.sub(r'ि([क-ह])', r'\1ि', text)
    
    # Fix double matras
    text = re.sub(r'ाा+', 'ा', text)
    text = re.sub(r'िि+', 'ि', text)
    text = re.sub(r'ीी+', 'ी', text)
    text = re.sub(r'ुु+', 'ु', text)
    text = re.sub(r'ूू+', 'ू', text)
    text = re.sub(r'ेे+', 'े', text)
    text = re.sub(r'ैै+', 'ै', text)
    text = re.sub(r'ोो+', 'ो', text)
    text = re.sub(r'ौौ+', 'ौ', text)
    
    # Fix multiple halants
    text = re.sub(r'्+', '्', text)
    
    # Fix orphan halant at end
    text = re.sub(r'्\s', ' ', text)
    
    return text


def is_likely_preeti(text: str) -> bool:
    """
    Return True when the text looks like it came from Preeti font encoding.
    Preeti uses specific ASCII chars that rarely appear together in English.
    """
    import re
    if not text or not text.strip():
        return False
    # Preeti-specific chars: backslash halant, special brackets used as matras
    preeti_signals = re.compile(r"[\\;lLfmosuv]")
    ascii_letters = sum(1 for c in text if c.isascii() and c.isalpha())
    if ascii_letters == 0:
        return False
    signal_hits = len(preeti_signals.findall(text))
    return signal_hits / max(ascii_letters, 1) > 0.20


# Precompiled for faster execution
_DEVANAGARI_RE = re.compile(r'[\u0900-\u097F]')


def conversion_quality(original: str, converted: str) -> dict:
    """Return a dict with devanagari_ratio (0-100) and char counts."""
    import re
    deva = re.compile(r"[\u0900-\u097F]")
    chars = [c for c in converted if c.strip()]
    if not chars:
        return {"devanagari_ratio": 0.0, "original_length": len(original),
                "converted_length": len(converted)}
    ratio = sum(1 for c in chars if deva.match(c)) / len(chars) * 100
    return {"devanagari_ratio": round(ratio, 1),
            "original_length": len(original),
            "converted_length": len(converted)}

if __name__ == "__main__":
    tests = [
        ("g]kfn", "नेपाल"),
        ("sf7df8f}+", "काठमाडौं"),
        (";"
         "//sf/", "सरकार"),
    ]
    for src, expected in tests:
        got = preeti_to_unicode(src)
        status = "PASS" if got == expected else f"FAIL (got {got!r})"
        print(f"{src!r} → {got!r}: {status}")
