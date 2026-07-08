"""Debug script for PDF extraction issues."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fitz
from app.extract.direct_extract import extract_page_direct, build_font_lookup
from app.nlp.font_detector import analyse_document_fonts
from app.legacy_fonts.converter import is_legacy_encoded, force_convert_legacy
from app.extract.direct_extract import _should_convert_span, _convert_span

PDF = r"d:\50\Tax Expert\_राजस्व न्याधिकरण ऐन २०३१_bpmacpv.pdf"


def main() -> None:
    with open(PDF, "rb") as f:
        data = f.read()

    fa = analyse_document_fonts(data)
    lookup = build_font_lookup(fa)
    doc = fitz.open(PDF)
    page = doc.load_page(0)
    blocks = [b for b in page.get_text("dict")["blocks"] if b.get("type") == 0]

    print("BLOCK 5 spans:")
    for s in blocks[5]["lines"][0]["spans"]:
        fn, t = s["font"], s["text"]
        should = _should_convert_span(t, fn, lookup.get(fn))
        conv = _convert_span(t, fn, lookup.get(fn)) if should else t
        print(f"  {fn}: {t[:50]!r} -> should={should} -> {conv[:60]!r}")

    result = extract_page_direct(page, lookup)
    print("\nFIRST 500 chars output:")
    print(result["text"][:500])
    doc.close()


if __name__ == "__main__":
    main()
