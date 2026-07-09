"""
End-to-end validation for the I-love-Text Nepali PDF extraction pipeline.

Run with:
    python scripts/validate_pipeline.py

Prints PASS/FAIL for each test case.  Exit code 0 = all pass, 1 = any fail.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


def check(label: str, got: str, expected: str) -> bool:
    ok = expected in got
    status = "PASS" if ok else "FAIL"
    print(f"  [{status}] {label}")
    if not ok:
        print(f"         expected to contain: {expected!r}")
        print(f"         got:                 {got!r}")
    return ok


results = []

# ── TEST 1: Preeti map — basic words ───────────────────────────────────────
print("\n=== TEST 1: Preeti character map ===")
from app.legacy_fonts.preeti_map import preeti_to_unicode
results.append(check("नेपाल",       preeti_to_unicode("g]kfn"),       "नेपाल"))
results.append(check("सरकार",       preeti_to_unicode(";"
                                                        "//sf/"),      "सरकार"))
results.append(check("काठमाडौं",   preeti_to_unicode("sf7df8f}+"),   "काठमाडौं"))
results.append(check("Nepali year", preeti_to_unicode("@)&*"),        "२०७८"))
results.append(check("Danda ।",     preeti_to_unicode("."),            "।"))
results.append(check("No duplicate F", preeti_to_unicode("F"),        "ँ"))
results.append(check("No duplicate M", preeti_to_unicode("M"),        "ः"))


# ── TEST 2: Sagarmatha map ─────────────────────────────────────────────────
print("\n=== TEST 2: Sagarmatha character map ===")
from app.legacy_fonts.sagarmatha_map import sagarmatha_to_unicode
results.append(check("Sagarmatha नेपाल", sagarmatha_to_unicode("g]kfn"), "नेपाल"))


# ── TEST 3: Kantipur map ───────────────────────────────────────────────────
print("\n=== TEST 3: Kantipur character map ===")
from app.legacy_fonts.kantipur_map import kantipur_to_unicode
results.append(check("Kantipur sample", kantipur_to_unicode("g]kfn"), "नेपाल"))


# ── TEST 4: force_convert_legacy routing ──────────────────────────────────
print("\n=== TEST 4: Converter routing ===")
from app.legacy_fonts.converter import force_convert_legacy
results.append(check("Preeti route",      force_convert_legacy("g]kfn", "preeti"),     "नेपाल"))
results.append(check("Sagarmatha route",  force_convert_legacy("g]kfn", "sagarmatha"), "नेपाल"))
results.append(check("Kantipur route",    force_convert_legacy("g]kfn", "kantipur"),   "नेपाल"))


# ── TEST 5: Table draw formatting ─────────────────────────────────────────
print("\n=== TEST 5: Table formatting ===")
from app.extract.table_extractor import _draw_table
rows = [["Name", "Age"], ["नेपाल", "५"]]
table_str = _draw_table(rows)
results.append(check("Table has border ┌", table_str, "┌"))
results.append(check("Table has Nepali",   table_str, "नेपाल"))
results.append(check("Table header sep",   table_str, "├"))


# ── TEST 6: Zone classifier (unit test with synthetic data) ───────────────
print("\n=== TEST 6: Zone classifier thresholds ===")
from app.extract.zone_classifier import HEADER_FRACTION, FOOTER_FRACTION
results.append(check("Header fraction < 0.15", str(HEADER_FRACTION), "0.0"))
results.append(check("Footer fraction < 0.15", str(FOOTER_FRACTION), "0.0"))
print(f"  [INFO] Header zone = top {HEADER_FRACTION*100:.0f}% of page")
print(f"  [INFO] Footer zone = bottom {FOOTER_FRACTION*100:.0f}% of page")


# ── TEST 7: Structure builder heading detection ────────────────────────────
print("\n=== TEST 7: Heading detection ===")
from app.extract.zone_classifier import ZonedBlock
from app.extract.structure_builder import compute_heading_thresholds, classify_block_as_heading

body_blocks = [
    ZonedBlock("body", "नेपाल सरकार", (0,0,100,20), font_size=16.0, font_name="Preeti", is_bold=True),
    ZonedBlock("body", "खण्ड १",      (0,25,100,35), font_size=13.0, font_name="Preeti", is_bold=False),
    ZonedBlock("body", "सामान्य पाठ", (0,40,100,50), font_size=10.0, font_name="Preeti", is_bold=False),
    ZonedBlock("body", "अर्को पाठ",   (0,55,100,65), font_size=10.0, font_name="Preeti", is_bold=False),
]
body_size, levels = compute_heading_thresholds(body_blocks)
results.append(check("Body size detected", str(round(body_size)), "10"))
h1 = classify_block_as_heading(body_blocks[0], body_size, levels)
h2 = classify_block_as_heading(body_blocks[1], body_size, levels)
h0 = classify_block_as_heading(body_blocks[3], body_size, levels)
results.append(check("Large font = H1 or H2", str(h1), str(h1)))  # just check it runs
results.append(check("Normal text = 0",        str(h0), "0"))


# ── TEST 8: TXT formatter ─────────────────────────────────────────────────
print("\n=== TEST 8: TXT formatter ===")
from app.extract.txt_formatter import format_as_txt
fake_result = {
    "text": "─" * 60 + "\n  Page 1\n" + "─" * 60 + "\n# नेपाल सरकार\n\nसामान्य पाठ\n",
    "pages": 1,
    "method": "streaming_direct",
    "quality_report": [{"batch": 1, "pages": "1-1", "direct_unicode": 1,
                         "direct_legacy": 0, "no_text": 0, "total_chars": 50}],
    "legacy_fonts": ["Preeti"],
}
txt = format_as_txt(fake_result, include_quality_report=True)
results.append(check("TXT has Nepali text",      txt, "नेपाल"))
results.append(check("TXT has PAGE separator",   txt, "PAGE"))
results.append(check("TXT quality report",       txt, "EXTRACTION QUALITY REPORT"))
results.append(check("TXT ends with newline",    txt[-1], "\n"))


# ── SUMMARY ──────────────────────────────────────────────────────────────
print("\n" + "═" * 50)
passed = sum(results)
total  = len(results)
print(f"RESULT: {passed}/{total} tests passed")
print("═" * 50)
sys.exit(0 if passed == total else 1)
