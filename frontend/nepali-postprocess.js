/**
 * Lightweight Nepali/Devanagari text normalization for client-side OCR
 * output.
 *
 * BACKGROUND: an audit of this repo's frontend found that the client-side
 * Tesseract.js OCR pipeline (ocr-client.js) applied ZERO Nepali-specific
 * post-processing -- raw Tesseract output was returned to the user as-is,
 * with none of the safety nets the Python backend has (NFC normalization,
 * zero-width character stripping, doubled-combining-mark collapse, etc.).
 * Since the client-side "Extract Text" button is likely the most-used
 * feature in the app, this gap meant most users never benefited from any
 * of that work.
 *
 * This module ports the SAFE, UNIVERSAL parts of that cleanup (the parts
 * that require no fuzzy-matching against a vocabulary and carry no risk
 * of "correcting" a word into a different, wrong word) to run entirely in
 * the browser:
 *   1. Unicode NFC normalization
 *   2. Zero-width character stripping (U+200B/U+200C/U+200D/U+FEFF)
 *   3. Doubled combining-mark collapse (matras U+093E-U+094C, plus
 *      chandrabindu/anusvara/visarga U+0901-U+0903) -- these never
 *      legitimately repeat back to back in standard Nepali orthography
 *   4. Whitespace cleanup (collapse runs of spaces/tabs within a line,
 *      preserve line breaks and intentional tab-separated columns)
 *
 * Deliberately NOT ported here: the backend's fuzzy word-level lexicon
 * correction (app.nlp.nepali_sentence_intelligence.repair_corrupted_
 * devanagari / app.intelligence.nepal_knowledge_base.correct_word). That
 * logic depends on a large, curated Nepali vocabulary and confidence
 * gating that would be expensive to duplicate and keep in sync in
 * JavaScript; running it only on the backend (for the Nepali Font ->
 * Unicode Converter flow) rather than half-reimplementing it client-side
 * is the safer choice for now.
 */
(function (global) {
  'use strict';

  const ZERO_WIDTH_RE = /[\u200B\u200C\u200D\uFEFF]/g;
  const MULTI_SPACE_RE = /[^\S\n]+/g;

  // Post-base vowel signs (matras): U+093E - U+094C (excludes U+094D
  // halant, which legitimately can appear standalone/repeated in some
  // conjunct sequences and should not be collapsed).
  const MATRA_RANGE_START = 0x093E;
  const MATRA_RANGE_END = 0x094C;
  // Chandrabindu, anusvara, visarga: these never legitimately repeat
  // back to back in standard Nepali orthography, so any consecutive
  // duplicate is safe to collapse to one.
  const NASALIZATION_MARKS = [0x0901, 0x0902, 0x0903];

  function buildDoubledMarkRegex() {
    let codepoints = '';
    for (let cp = MATRA_RANGE_START; cp <= MATRA_RANGE_END; cp++) {
      codepoints += String.fromCharCode(cp);
    }
    for (const cp of NASALIZATION_MARKS) {
      codepoints += String.fromCharCode(cp);
    }
    // Escape for use inside a character class (none of these codepoints
    // need escaping individually, but keep this defensive in case the
    // range is ever extended to include regex-special characters).
    const escaped = codepoints.replace(/[\]\\^-]/g, '\\$&');
    return new RegExp(`([${escaped}])\\1+`, 'g');
  }

  const DOUBLED_MARK_RE = buildDoubledMarkRegex();

  function collapseDoubledMarks(text) {
    return text.replace(DOUBLED_MARK_RE, '$1');
  }

  function cleanWhitespace(text) {
    return text
      .split('\n')
      .map((line) => {
        if (line.includes('\t')) {
          return line.split('\t').map((col) => col.replace(MULTI_SPACE_RE, ' ').trim()).join('\t');
        }
        return line.replace(MULTI_SPACE_RE, ' ').trim();
      })
      .join('\n')
      .trim();
  }

  /**
   * Normalize OCR output text. Safe to call on any text, including
   * non-Nepali/English-only text (all steps are no-ops on plain ASCII).
   */
  function normalize(text) {
    if (!text) return '';
    let out = text.normalize('NFC');
    out = out.replace(ZERO_WIDTH_RE, '');
    out = collapseDoubledMarks(out);
    out = out.replace(/\r\n/g, '\n').replace(/\r/g, '\n');
    out = cleanWhitespace(out);
    return out;
  }

  global.NepaliPostprocess = { normalize };
})(window);
