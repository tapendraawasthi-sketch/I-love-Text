# Golden accuracy fixtures

Add pairs:
- `name.gold.txt` — expected as-is Unicode output
- `name.out.txt` — pipeline output to compare

Run:
```
python scripts/cer_check.py --dir tests/golden --max-cer 0.005
```

Recommended fixtures (add real PDFs under `tests/fixtures/`):
- g01_unicode_letter
- g02_preeti_gazette
- g03_kantipur_notice
- g04_mixed_eng_nep
