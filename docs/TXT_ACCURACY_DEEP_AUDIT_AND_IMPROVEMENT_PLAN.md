# I Love Text / TextExtract — Deep Accuracy Audit & Improvement Plan

**Repository:** [tapendraawasthi-sketch/I-love-Text](https://github.com/tapendraawasthi-sketch/I-love-Text)  
**Live deploy:** `https://i-love-text-1.onrender.com`  
**Audit date:** 2026-07-14  
**Implementation status:** Phase 0–2 core accuracy fixes landed in codebase (see §15)

**Goal:** Maximum-fidelity `.txt` output for uploaded PDF / DOCX / image files, with special focus on Nepali (Unicode Devanagari + legacy fonts Preeti / Kantipur / Sagarmatha / etc.)

---

## 0. Executive verdict

The product does **not** currently deliver “as-is” `.txt`. It delivers **reconstructed, post-corrected Unicode Nepali** optimized for readability. That design causes many of the Nepali errors you see.

| Claim users expect | What the code actually does |
|---|---|
| Exact text of my file as `.txt` | Rebuilds lines from spans / OCR boxes, injects placeholders, applies fuzzy dictionary “repairs” |
| Perfect Nepali | Tesseract `nep` + incomplete legacy maps + unsupervised Levenshtein word swaps |
| One reliable pipeline | Three conflicting strategies (direct convert vs force-OCR vs raw PyMuPDF consensus) |

### Honest accuracy ceiling (important)

| Document type | Achievable ceiling | Current reality |
|---|---|---|
| Digital Unicode PDF | ~99.9% (near-perfect) | Usually good; still mutated by KB / cross-page / matra “fixes” |
| Digital Preeti / Kantipur PDF (clean text layer) | ~98–99.5% with correct font map | Often good on smart-txt path; degraded when fallback OCR or wrong map wins |
| Mixed legacy + Unicode pages | High if per-span routing is correct | Fragile — wrong font guess = garbage or wrong Devanagari |
| Scanned / image PDF / photos | 85–97% depending on scan quality | Tesseract limits + aggressive repairs that can make errors worse |
| Handwriting / stamps / seals | Not reliably extractable as text | Placeholder or OCR noise |

**There is no system that guarantees zero error on arbitrary scans.** What you *can* build is:

1. **Forensic / as-is mode** — never invent or swap words; only convert encoding when proven.  
2. **Cleaned mode** — optional repairs, clearly labeled.  
3. **Measurable accuracy** — golden fixtures + Character Error Rate (CER) gates so regressions cannot ship.

This document is the blueprint for that.

---

## 1. Product surface today

### Backend endpoints (`app/main.py`)

| Endpoint | Purpose | Accuracy relevance |
|---|---|---|
| `POST /api/extract` | JSON `{ text, meta }` | Main extraction; modes `auto` / `direct` / `ocr` |
| `POST /api/extract-txt` | Downloads `.txt` | **Always forces `mode="auto"`** — user cannot choose direct-only |
| `POST /api/extract/smart-txt` | PDF font-layer → Unicode `.txt` | Best path for digital Preeti; still can apply repairs / optional AI |
| `POST /api/convert-to-image-pdf` | Rasterize PDF | Useful for vision LLMs; not text fidelity |
| `POST /api/detect-fonts` | Font report | Diagnostic only |
| `POST /api/analyze`, `/api/segment` | Structure intel | Feeds routing decisions |

### Frontend (`frontend/index.html`)

Three user-facing buttons with **different philosophies**:

1. **Extract Text** — OCR-oriented “max quality”  
2. **Convert to Image PDF** — admit OCR is weak; push users to ChatGPT/Claude  
3. **Font → Unicode Converter** — correct approach for digital legacy PDFs  

The UI itself signals that `.txt` accuracy is unreliable. A production-grade product should make **as-is forensic extract** the primary button for digital documents, and OCR only for true scans.

### Stack

- FastAPI + PyMuPDF (`fitz`) + Tesseract (`pytesseract`) + OpenCV preprocess  
- Legacy: `npttf2utf` + built-in Preeti/Kantipur/Sagarmatha maps  
- Optional: Ollama / LangChain (README says “no AI”; `requirements.txt` includes AI libs)  
- Deploy: Render (`render.yaml`) + Docker

---

## 2. End-to-end architecture (current)

```
Upload (PDF/DOCX/image)
        │
        ▼
┌───────────────────────┐
│ Document Intelligence │  analyze_document / page_segmenter
│ fonts, scan, domains  │
└───────────┬───────────┘
            ▼
┌───────────────────────┐
│ Per-block OCR router  │  app/intelligence/ocr_router.py
│ convert | OCR | table │  skip | placeholder
└───────────┬───────────┘
            ▼
┌───────────────────────┐
│ Cross-page corrections│  edit-distance word swaps  ← MUTATES TEXT
└───────────┬───────────┘
            ▼
┌───────────────────────┐
│ Knowledge-base fuzzy  │  correct_word ≥75 conf     ← MUTATES TEXT
│ word replace          │
└───────────┬───────────┘
            ▼
┌───────────────────────┐
│ format_as_txt / JSON  │  page separators, labels
└───────────────────────┘
```

### Fallback chain when intelligence fails (`pdf_handler.py`)

```
reconstruct_document()
  → self_repair_page()
  → streaming_extractor (large PDFs)
  → document_ocr()
```

Each fallback has a **different policy** for legacy fonts. Same PDF can yield different Nepali text depending on which path wins.

### Conflicting philosophies (critical weakness)

| Path | Legacy Preeti PDF policy |
|---|---|
| `ocr_router` + `direct_extract` | Convert text layer via maps / npttf2utf |
| `precision_pipeline` | **Reject** text layer; force OCR |
| `multi_engine_extractor` | Pick best raw PyMuPDF dump; **no font conversion** |

This alone explains “sometimes good, sometimes garbage” for Nepali PDFs.

---

## 3. How `.txt` is produced today

### Encoding & transport

- `/api/extract-txt` returns UTF-8 `text/plain; charset=utf-8`  
- **No UTF-8 BOM** (OK for most editors; Excel may mis-detect)  
- Filename: ASCII-sanitized `*_extracted.txt`

### Formatting bugs (`app/extract/txt_formatter.py`)

Main pipeline joins pages with:

```text
--- Page Break ---
```

(`pdf_handler.py`)

But `format_as_txt` only rewrites separators matching:

```text
──────────────────────────────
Page N
──────────────────────────────
```

**Result:** the download `.txt` often keeps raw `--- Page Break ---` and never gets the documented `═══ PAGE N ═══` separators.

### Layout is reconstructed, not preserved

- Direct extract: span x-gaps → spaces/tabs (`gap > 50` → tab)  
- OCR: Tesseract word boxes → lines (`app/ocr/layout.py`)  
- Tables: separate extractors; merge can scramble order (see §5.3)

This is **not** a byte-faithful export of the document’s text stream.

---

## 4. Ranked root causes of Nepali / `.txt` errors

Ordered by severity for “not as-is accurate”:

| Rank | Root cause | Severity | User-visible symptom |
|---|---|---|---|
| 1 | Unsupervised post-correction (KB, lexicon fuzzy, cross-page) | Critical | Correct Nepali words replaced by common legal words |
| 2 | Conflicting pipelines (convert vs OCR vs raw dump) | Critical | Same file → different wrong output on retries / fallbacks |
| 3 | Incomplete legacy maps + Preeti fallback for unknown fonts | Critical | Mixed garbage ASCII + wrong Devanagari |
| 4 | `precision_pipeline` forces OCR on digital legacy PDFs | High | Soft OCR errors on text that was already extractable |
| 5 | Table/block `zip` misalignment in `extract_page_direct` | High | Scrambled reading order when tables exist |
| 6 | Tesseract Nepali OCR limits (PSM, binarization, mixed lang) | High | Matra errors, broken shirorekha, English headers mangled |
| 7 | Weak font detection (unknown → Preeti @ low confidence) | High | Wrong conversion map applied |
| 8 | Placeholder injection (`[Signature]`, `[Figure]`, stamps) | Medium | Extra tokens not in source |
| 9 | `.txt` formatter page-break inconsistency | Medium | Ugly / inconsistent downloads |
| 10 | No golden fixtures / CER gates in CI | Medium | Regressions ship unnoticed |
| 11 | Deploy: missing `nep` pack, tessdata tier mismatch, Render OOM | Medium | English-only OCR or truncated pages |
| 12 | Optional LLM paraphrase path | Low–Med | Can rewrite meaning if enabled |

---

## 5. Detailed weakness catalogue (with code evidence)

### 5.1 Knowledge-base word substitution corrupts valid text

**File:** `app/extract/pdf_handler.py` → `_apply_post_extraction_intelligence`

```python
words = re.findall(r"[\u0900-\u097F]+", text)
for word in words:
    corrected, conf, source = correct_word(word, doc_intel.domain)
    if corrected != word and conf >= 75:
        text = text.replace(word, corrected, 1)
```

**File:** `app/intelligence/nepal_knowledge_base.py` → `correct_word`

- Edit distance **1** → confidence **85** → auto-applied  
- Edit distance **2** → confidence **65** → not applied by threshold, but still available  

**Failure mode:** Rare names, village names, company names, statute titles not in the lexicon get replaced by frequent legal/accounting words. `str.replace(..., 1)` has no word-boundary safety.

**Fix direction:** Disable by default in as-is mode. If enabled, require exact skeleton match or human confirm; log every change in `meta.corrections[]`.

---

### 5.2 OCR “repair” uses aggressive lexicon fuzzy match

**File:** `app/ocr/nepali_postprocess.py`

```python
if _DEVANAGARI_RE.search(text) and corruption_score(text) > 0:
    text = repair_corrupted_devanagari(text)
```

`corruption_score > 0` is an extremely low bar (any replacement/PUA character).

**File:** `app/nlp/nepali_sentence_intelligence.py` → `repair_corrupted_devanagari`

- Applies hard-coded `CONTEXT_REPAIRS` legal phrase templates  
- For every Devanagari token ≥3 chars, finds closest `DOMAIN_LEXICON` entry with up to **~45%** skeleton edit distance  

**Failure mode:** Valid uncommon words become `नियमवाली`, `राजपत्र`, `ऐन`, etc. This is the opposite of as-is fidelity.

**Fix direction:** Only run when PUA/garbage density is high (e.g. score > 0.15). Remove lexicon substitution from default OCR path entirely for forensic mode.

---

### 5.3 Cross-page singleton “corrections”

**File:** `app/intelligence/cross_page.py` (applied from `pdf_handler.py` when pages > 2)

Singleton words with edit distance 1 from frequent words (≥3–5 occurrences) get overwritten.

**Failure mode:** Legitimate spelling variants and near-homoglyphs across pages are forced to the majority form.

---

### 5.4 Unicode / matra “repairs” can break correct conjuncts

**File:** `app/extract/unicode_validator.py` (invoked from direct extract repair path)

Regex redistribution of matras assumes specific PDF extraction order bugs. When order is already correct, patterns can still fire and damage conjuncts.

---

### 5.5 Legacy font conversion gaps

**Files:**  
`app/legacy_fonts/converter.py`, `preeti_map.py`, `kantipur_map.py`, `sagarmatha_map.py`, `mappings.py`

| Font | Built-in map depth | npttf2utf | Dangerous fallback |
|---|---|---|---|
| Preeti (+ many aliases) | Rich | Yes | — |
| Kantipur | Thin (~50) | Yes | Preeti if npttf fails |
| Sagarmatha | Thin; self-test comments show CHECK not PASS | Yes | **Preeti map** |
| Himali / Aakriti / PCS | None | Yes only | Preeti |

Additional gaps:

1. Many font names collapsed to Preeti in `NPTTF2UTF_FONT_MAP`.  
2. npttf2utf accepted with only **10%** Devanagari ratio (`converter.py`).  
3. Unknown fonts → `guess_font_from_text` → Preeti @ **35%** confidence (`font_detector.py`).  
4. Font-program / ToUnicode cmap parsing exists in self-repair / reconstruction but **not** as primary path.  
5. Map guessing tries Preeti + Kantipur and picks higher heuristic score — wrong map can win.

---

### 5.6 `precision_pipeline` OCRs digital legacy PDFs

**File:** `app/extract/precision_pipeline.py`

When legacy font metadata is detected, digital text extraction returns `None` and forces OCR. That discards an exact (encoding-mapped) text layer and introduces OCR error for clean Preeti PDFs.

---

### 5.7 Multi-engine consensus can prefer raw ASCII garbage

**File:** `app/extract/multi_engine_extractor.py`

Picks best PyMuPDF text dump without mandatory font conversion. Quality heuristics can prefer longer raw ASCII over shorter correct Unicode.

**Related:** `direct_extract.extract_document_high_accuracy` may choose `unicode_passthrough` when raw scores higher than converted by +3.0 — for non-Preeti-dominant legacy docs this ships unconverted ASCII.

---

### 5.8 Table / block ordering bug

**File:** `app/extract/direct_extract.py` (~`extract_page_direct`)

`blocks_output` excludes table-overlapping blocks, but code zips against full `page_dict["blocks"]`. Y-order merge becomes misaligned → scrambled `.txt` when tables exist (common in Nepali government / accounting PDFs).

---

### 5.9 OCR engine & preprocess weaknesses

**Config:** `app/config.py`

- OEM 1, PSM 3 primary; retries PSM 6/4/11/12  
- DPI 400 default; dimensions up to 5000  
- `is_fast_ocr_mode` always `False` (good for quality; bad for Render memory)

**Engine:** `app/ocr/engine.py`

- `auto` → `nep+eng` if both installed  
- Scoring boosts Devanagari ratio — can prefer hallucinated Devanagari-like noise  

**Preprocess:** `app/ocr/preprocess.py`

- Otsu / adaptive binarization can clip shirorekha and thin matras  
- Upscale to `MIN_OCR_DIMENSION=2800` can blur low-res scans  

**Layout:** `app/ocr/layout.py`

- Keeps low-confidence Devanagari with threshold −15 → more garbage in output  

**Deploy mismatch:**

- Docker may use `tessdata_best`  
- `packages.txt` apt packs may be older/faster models  
- `TESSDATA_PREFIX` defaults to Linux path — Windows local runs can silently degrade  
- Missing `nep` pack → startup **warning only**, extraction continues  

---

### 5.10 Mixed English + Nepali

- API accepts `eng+nep` / `nep+eng` but no script-aware second pass  
- Latin acronyms, URLs, emails in government letterheads often get mangled when treated as Preeti  
- `is_plain_ascii_text` helps for URLs/emails but not for short English headers inside legacy spans  

---

### 5.11 Placeholders destroy as-is fidelity

**File:** `app/intelligence/ocr_router.py`

Signatures, QR, barcodes, figures, charts → `[Signature]`, `[Figure]`, etc. Useful for readability; fatal for “exact text of file”.

---

### 5.12 `/api/extract-txt` cannot select extraction mode

Always calls `extract_pdf(..., "auto")`. Users who need pure direct conversion for digital PDFs cannot get it through the `.txt` download endpoint without using smart-txt.

---

### 5.13 Test / validation gaps

**Current tests** cover converter smoke, lang resolution, layout, some API cases.

**Missing:**

- Golden PDF fixtures (Preeti, Kantipur, Unicode, scanned Nepali, mixed Eng+Nep, tables)  
- Exact expected `.txt` with CER/WER thresholds  
- Tests for `/api/extract-txt` page separators  
- Tests that KB / lexicon repairs do **not** fire on clean Unicode  
- CI gate: fail if CER rises  

`benchmark_framework.py` exists but is not wired into CI.

Validators (`extraction_validator`, `semantic_validator`, `confidence_scorer`) are **advisory** — low confidence still ships to the user.

---

### 5.14 Deployment risks (Render)

| Risk | Impact |
|---|---|
| Free-tier RAM + DPI 400 + max dim 5000 | OOM → 507 / truncated output |
| `nep` tessdata missing | English-biased OCR |
| Ollama install `|| true` in Docker | Silent AI disable while UI promises enhancement |
| README vs requirements contradiction | Maintainers misconfigure |

---

## 6. Target architecture: “Maximum Accuracy .txt”

### 6.1 Product modes (must separate)

| Mode ID | Name | Behavior | Default? |
|---|---|---|---|
| `forensic` | As-is / Forensic | Encoding conversion only when font proven; **zero** dictionary/cross-page/lexicon/LLM edits; no decorative placeholders | **Yes for .txt** |
| `balanced` | Cleaned Unicode | Safe NFC normalize + proven encoding convert; optional header/footer strip | Secondary |
| `ocr_max` | Scan OCR Max | Multi-PSM / multi-DPI OCR; postprocess only for PUA garbage; no lexicon swaps | Scans only |
| `assisted` | Assisted Repair | Current KB / lexicon / optional LLM — **opt-in**, every edit listed in manifest | Off |

### 6.2 Single decision tree (replace today’s conflicts)

```
1. Open document; build page inventory
2. Per page classify:
     A. Has selectable text layer?
        YES → extract spans with font IDs
             → per span:
                  Unicode Devanagari/Latin → keep (NFC)
                  Proven legacy font → convert with THAT map only
                  Unknown ASCII-looking → mark UNCERTAIN; try cmap/font-program;
                       if still unknown → do NOT Preeti-guess; OCR that region
        NO  → render region at ≥350 DPI → OCR with nep+eng
3. Never run precision_pipeline “legacy ⇒ OCR” if conversion quality passes gate
4. Never run multi_engine raw dump without conversion for Nepali legacy
5. Emit .txt + sidecar JSON manifest (optional) of every decision
```

### 6.3 Quality gates before shipping text

For each page / block:

| Gate | Pass condition |
|---|---|
| Encoding gate | Converted text Devanagari ratio ≥ threshold **and** leftover Preeti marker density low |
| OCR gate | Mean confidence ≥ threshold; else retry PSM/DPI |
| Corruption gate | PUA / U+FFFD density below limit |
| Order gate | Reading order from segmenter, not broken zip |
| Mutation gate (forensic) | `corrections_applied == 0` |

If gate fails → return partial text + explicit `meta.warnings[]` (never silent wrong text).

---

## 7. Huge improvement plan (phased)

### Phase 0 — Stop the bleeding (1–3 days) — **P0**

| # | Task | Files | Acceptance |
|---|---|---|---|
| 0.1 | Add `fidelity=forensic\|balanced\|assisted` form field; default forensic for `/api/extract-txt` | `main.py`, `pdf_handler.py` | Forensic download has zero KB/lexicon/cross-page edits |
| 0.2 | Disable `_apply_post_extraction_intelligence` word replace unless `assisted` | `pdf_handler.py` | Clean Unicode PDF unchanged vs PyMuPDF text |
| 0.3 | Gate `repair_corrupted_devanagari` behind high corruption score + `assisted`/`ocr_max` | `nepali_postprocess.py` | No lexicon swaps on clean OCR |
| 0.4 | Disable cross-page corrections in forensic | `pdf_handler.py`, `cross_page.py` | Multi-page names preserved |
| 0.5 | Fix page separator handling for `--- Page Break ---` | `txt_formatter.py` | Download uses consistent separators |
| 0.6 | Fix table/block zip ordering bug | `direct_extract.py` | Table pages keep reading order |
| 0.7 | Pass `mode` through `/api/extract-txt` | `main.py`, frontend | User can force direct/OCR |

### Phase 1 — Unify pipelines (3–7 days) — **P0/P1**

| # | Task | Files | Acceptance |
|---|---|---|---|
| 1.1 | Make `direct_extract` the only primary digital path | `pdf_handler`, `ocr_router` | One code path for digital PDFs |
| 1.2 | Change `precision_pipeline`: legacy digital → convert first; OCR only if conversion fails gate | `precision_pipeline.py` | Clean Preeti PDF CER ≈ 0 vs converted gold |
| 1.3 | Ban raw legacy ASCII as multi-engine winner | `multi_engine_extractor.py` | Winner always Unicode for Nepali legacy |
| 1.4 | Remove Preeti fallback for known Kantipur/Sagarmatha fonts | `converter.py` | Wrong-map rate drops |
| 1.5 | Raise npttf2utf quality gate from 10% to ≥40% Devanagari (with exceptions for short tokens/dates) | `converter.py` | Low-quality converts rejected → OCR region |
| 1.6 | Unknown font: never default Preeti; mark uncertain + OCR glyph region | `font_detector.py`, `pdf_font_utils.py` | No silent wrong map |
| 1.7 | Wire font-program / cmap parser into main path | `font_program_parser.py`, router | Embedded custom fonts convert correctly |

### Phase 2 — Nepali OCR excellence (1–2 weeks) — **P1**

| # | Task | Detail |
|---|---|---|
| 2.1 | Block-level language | Devanagari-dominant blocks: `nep` only second pass; Latin blocks: `eng` |
| 2.2 | PSM policy | Paragraphs `psm 6`; sparse `psm 11`; tables dedicated path |
| 2.3 | Preprocess A/B | Skip binarization on digital renders; keep grayscale for Devanagari; keep adaptive only for dirty scans |
| 2.4 | DPI policy | Scans ≥350; never downscale below readable shirorekha; memory-aware on Render |
| 2.5 | Layout | Raise garbage threshold for low-conf Latin; keep careful Devanagari rules without −15 blanket |
| 2.6 | Optional engines | Evaluate RapidOCR / PaddleOCR / EasyOCR as **secondary voters**, not replacements; consensus only when Tesseract conf low |
| 2.7 | Consider `tessdata_best` only | Pin same model in Docker + packages docs |

### Phase 3 — Legacy font perfection (1–2 weeks) — **P1**

| # | Task | Detail |
|---|---|---|
| 3.1 | Expand Kantipur & Sagarmatha maps to Preeti completeness | Include conjuncts, matras, digits, punctuation |
| 3.2 | Vendored `map.json` from pinned `npttf2utf` version | Reproducible builds |
| 3.3 | Add Himali / Aakriti / PCS maps or official npttf names | Drop Preeti last-resort |
| 3.4 | Per-span conversion tests | Fixture strings from real Nepal Gazette / invoice PDFs |
| 3.5 | Visual verification option | Render glyph vs Unicode overlay for QA (dev tool) |

### Phase 4 — Measurement & CI (ongoing) — **P0 for quality culture**

Create `tests/golden/`:

| Fixture ID | Type | Must pass |
|---|---|---|
| `g01_unicode_letter.pdf` | Digital Unicode | CER = 0 vs gold `.txt` |
| `g02_preeti_gazette.pdf` | Digital Preeti | CER ≤ 0.5% forensic |
| `g03_kantipur_notice.pdf` | Digital Kantipur | CER ≤ 1% |
| `g04_mixed_eng_nep.pdf` | Mixed | Latin + Devanagari both intact |
| `g05_scan_nepali_300dpi.pdf` | Scan | CER ≤ 5% (document quality dependent) |
| `g06_table_budget.pdf` | Tables | Reading order + cell text |
| `g07_docx_nepali.docx` | DOCX | Exact paragraphs |
| `g08_image_photo.jpg` | Photo | Soft threshold + warnings |

Wire `benchmark_framework.py` into GitHub Actions / local `scripts/validate_pipeline.py`.

**Hard rule:** no merge if forensic CER on g01–g04 regresses.

### Phase 5 — Product / UX (3–5 days) — **P1**

| # | Change |
|---|---|
| 5.1 | Primary button: **Download as-is .txt (Forensic)** |
| 5.2 | Secondary: Cleaned Unicode / OCR Max |
| 5.3 | Show font detection before extract (“Detected: Preeti 92% — conversion path”) |
| 5.4 | Show correction manifest when assisted mode used |
| 5.5 | Warn when `nep` tessdata missing (block Nepali claims) |
| 5.6 | Health endpoint: `degraded: true` if nep/npttf broken |

### Phase 6 — Ops hardening — **P2**

| # | Change |
|---|---|
| 6.1 | Fail closed if required language packs missing for requested `lang` |
| 6.2 | Auto-scale DPI by page count & available RAM |
| 6.3 | Align README with actual AI/optional deps |
| 6.4 | Rotate any leaked GitHub PATs in remotes; use SSH or credential manager |
| 6.5 | Add UTF-8 BOM option for Excel users |
| 6.6 | Structured logging of route decisions per page |

---

## 8. Concrete API contract proposal

### `POST /api/extract-txt` (revised)

```http
POST /api/extract-txt
Content-Type: multipart/form-data

file: <binary>
lang: auto|eng|nep|eng+nep
mode: auto|direct|ocr
fidelity: forensic|balanced|assisted
page_separators: true|false
include_placeholders: false   # default false in forensic
include_correction_manifest: true|false
bom: false|true
```

### Response headers

```http
Content-Type: text/plain; charset=utf-8
Content-Disposition: attachment; filename="doc_extracted.txt"
X-Extract-Method: direct_legacy_convert
X-Mean-Confidence: 99.2
X-Fidelity: forensic
X-Warnings-Count: 0
```

### Optional sidecar (`Accept: multipart/mixed` or `?sidecar=json`)

```json
{
  "pages": [
    {
      "page": 1,
      "route": "legacy_conversion",
      "font": "preeti",
      "confidence": 99.1,
      "gates": {"encoding": "pass", "corruption": "pass"},
      "corrections": []
    }
  ],
  "document": {
    "is_scanned": false,
    "legacy_fonts": ["Preeti"],
    "fidelity": "forensic"
  }
}
```

---

## 9. Implementation sketch (forensic path)

Pseudo-code for the target PDF path:

```python
def extract_pdf_forensic(pdf_bytes, lang="auto"):
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    pages_out = []
    warnings = []

    for page in doc:
        spans = extract_spans_with_fonts(page)
        if not spans and page_looks_blank(page):
            # true scan
            text, conf, w = ocr_page_region(page, lang, fidelity="ocr_max")
            warnings.extend(w)
        else:
            parts = []
            for span in spans:
                info = resolve_font_info(span.font)  # cmap/name/cmap — no Preeti guess
                if info.encoding == "unicode":
                    parts.append(nfc(span.text))
                elif info.encoding == "legacy" and info.map_name:
                    converted = convert_strict(span.text, info.map_name)
                    if quality_gate(converted):
                        parts.append(converted)
                    else:
                        # uncertain → OCR just this bbox
                        t, c, w = ocr_bbox(page, span.bbox, lang)
                        parts.append(t)
                        warnings.extend(w)
                else:
                    t, c, w = ocr_bbox(page, span.bbox, lang)
                    parts.append(t)
                    warnings.extend(w)
            text = assemble_reading_order(parts, spans)

        pages_out.append(text)

    # NO knowledge-base, NO cross-page, NO lexicon repair
    return join_pages(pages_out), warnings
```

---

## 10. Nepali-specific linguistic checklist

Any “perfect Nepali” claim must pass:

1. **Shirorekha continuity** — no broken headlines from binarization  
2. **Matra position** — ि ी े ै ो ौ correctly attached  
3. **Halant / conjuncts** — क्ष त्र ज्ञ श्र etc.  
4. **Nukta letters** where used  
5. **Devanagari digits** `०-९` vs Arabic `0-9` preserved as in source  
6. **Danda** `।` vs ASCII period  
7. **Preeti date codes** like `@)#!.$.!*` → correct BS dates  
8. **Mixed Latin** — PAN, VAT, email, URL untouched  
9. **Proper names** never fuzzy-matched to lexicon  
10. **Legal citations** (`दफा`, `ऐन`, `नियम`) only changed if encoding conversion requires it — never by fuzzy guess  

---

## 11. What “perfect” means for this product (definition of done)

Ship when all are true:

- [ ] Forensic mode is default for `.txt` download  
- [ ] Golden suite g01–g04 CER within thresholds in CI  
- [ ] No automatic dictionary/cross-page/lexicon edits in forensic  
- [ ] One digital extraction pipeline; OCR only for image regions / failed gates  
- [ ] Kantipur & Sagarmatha maps complete enough that Preeti fallback is removed for those families  
- [ ] Unknown fonts never silently mapped to Preeti  
- [ ] `nep` tessdata required for Nepali requests (hard fail or explicit degraded mode)  
- [ ] Frontend explains path: Convert vs OCR vs Forensic  
- [ ] Every non-forensic edit listed in a manifest  
- [ ] Render memory policy prevents silent OOM truncation  

Until then, marketing copy should say **“high-accuracy Unicode extraction”**, not **“perfect / error-free”**.

---

## 12. Recommended immediate backlog (this week)

1. Implement **forensic fidelity** flag and turn off mutations by default.  
2. Fix **table zip bug** + **page separator** bug.  
3. Stop **Preeti default** for unknown fonts.  
4. Add **4 golden PDFs** + CER script.  
5. Make `/api/extract-txt` accept `mode` + `fidelity`.  
6. Update frontend: primary = Forensic .txt; secondary = OCR.  
7. Rotate GitHub credentials if a PAT was ever stored in `git remote -v`.  

---

## 13. File map (where to change what)

| Concern | Primary files |
|---|---|
| TXT download API | `app/main.py` |
| TXT formatting | `app/extract/txt_formatter.py` |
| PDF orchestration | `app/extract/pdf_handler.py` |
| Direct / legacy convert | `app/extract/direct_extract.py`, `app/legacy_fonts/*` |
| Block routing | `app/intelligence/ocr_router.py`, `page_segmenter.py` |
| False corrections | `nepal_knowledge_base.py`, `nepali_sentence_intelligence.py`, `cross_page.py` |
| OCR | `app/ocr/engine.py`, `preprocess.py`, `nepali_postprocess.py` |
| Conflicting fallbacks | `precision_pipeline.py`, `multi_engine_extractor.py`, `reconstruction_engine.py` |
| Frontend | `frontend/index.html`, JS that calls extract |
| Deploy | `Dockerfile`, `render.yaml`, `packages.txt`, `app/config.py` |
| Proof | `tests/golden/*`, `scripts/validate_pipeline.py`, `app/extract/benchmark_framework.py` |

---

## 14. Summary

**I Love Text is over-correcting.** Layers meant to “fix” Nepali (knowledge base, lexicon fuzzy match, cross-page edits, forced OCR on legacy fonts, Preeti fallback) are a major source of wrong `.txt` output — especially for Nepali.

**Maximum accuracy requires subtraction first, then precision:**

1. Stop mutating text by default.  
2. Convert only with proven font maps.  
3. OCR only image-backed regions.  
4. Measure with golden Nepali fixtures.  
5. Separate forensic `.txt` from optional cleaned/assisted modes.

That is the path to the highest practical accuracy. Absolute zero-error on every upload (including dirty phone photos) is not a realistic engineering claim; **zero unjustified mutation on digital Nepali PDFs** is achievable and should be the product’s north star.

---

## 15. Implementation status (2026-07-14)

Landed in code:

| Item | Status |
|------|--------|
| `fidelity` modes (`forensic` default) | Done — `app/extract/fidelity.py` |
| Disable KB / cross-page / lexicon in forensic | Done |
| `/api/extract-txt` accepts `mode` + `fidelity` + `bom` | Done |
| Page-break normalization in `txt_formatter` | Done |
| Table/block zip ordering fix | Done |
| Stop unknown→Preeti default | Done |
| Raise npttf2utf quality gate; no Preeti fallback for unknown maps | Done |
| `precision_pipeline` converts legacy before OCR | Done |
| Multi-engine prefers converted Unicode over raw ASCII | Done |
| Placeholders skipped in forensic | Done |
| Health `degraded` when `nep` / npttf missing | Done |
| Frontend primary **Download as-is .txt** | Done |
| CER helper + golden folder | Done — `scripts/cer_check.py`, `tests/golden/` |
| Unit tests `tests/test_accuracy_fidelity.py` | Done (26 related tests passing) |

Still optional / follow-up:

- Expand Kantipur/Sagarmatha maps to Preeti completeness
- Add real PDF golden fixtures under `tests/fixtures/`
- Wire CER into CI
- Memory-aware DPI auto-scale on Render
- Optional RapidOCR/Paddle secondary voters

---

*End of audit & improvement plan.*
