/**
 * Precision text extraction — digital text layer first, OCR only when needed.
 * Unicode PDFs: exact text. Preeti/Kantipur: font conversion. Scans: image OCR.
 */
(function (global) {
  'use strict';

  const LEGACY_FONT_RE = /preeti|kantipur|sagarmatha|himali|pcs|aakriti|fontasy|ganesh|navjeevan|kanchan|siddhi|vishwash|ekantipur/i;
  const DEVANAGARI_RE = /[\u0900-\u097F]/;

  function devanagariRatio(text) {
    const letters = [...text].filter((c) => /\S/.test(c));
    if (!letters.length) return 0;
    return letters.filter((c) => DEVANAGARI_RE.test(c)).length / letters.length;
  }

  function isLegacyFontName(name) {
    return LEGACY_FONT_RE.test(name || '');
  }

  function extractLinesFromTextContent(textContent) {
    const items = (textContent.items || []).filter((it) => it.str && it.str.trim());
    if (!items.length) return { lines: [], fonts: new Set() };

    const fonts = new Set();
    const sorted = [...items].sort((a, b) => {
      const dy = b.transform[5] - a.transform[5];
      if (Math.abs(dy) > 2) return dy;
      return a.transform[4] - b.transform[4];
    });

    const rows = [];
    let current = [];
    let lastY = null;

    for (const item of sorted) {
      fonts.add(item.fontName || '');
      const y = Math.round(item.transform[5]);
      if (lastY === null || Math.abs(y - lastY) > 4) {
        if (current.length) rows.push(current);
        current = [item];
        lastY = y;
      } else {
        current.push(item);
      }
    }
    if (current.length) rows.push(current);

    const lines = rows.map((row) => {
      row.sort((a, b) => a.transform[4] - b.transform[4]);
      let line = '';
      for (let i = 0; i < row.length; i++) {
        const chunk = row[i].str;
        if (i === 0) {
          line += chunk;
          continue;
        }
        const prev = row[i - 1];
        const gap = row[i].transform[4] - (prev.transform[4] + (prev.width || 0));
        const height = Math.max(8, Math.abs(prev.transform[3]) || 12);
        line += gap > height * 1.4 ? '\t' + chunk : (gap > 2 ? ' ' + chunk : chunk);
      }
      return line.trim();
    }).filter(Boolean);

    return { lines, fonts };
  }

  function convertLegacyText(text, fonts) {
    const fontList = [...fonts];
    const legacyFont = fontList.find(isLegacyFontName);
    if (legacyFont && global.preetiToUnicode) {
      return global.preetiToUnicode(text);
    }
    if (global.isLikelyPreeti && global.isLikelyPreeti(text) && global.preetiToUnicode) {
      return global.preetiToUnicode(text);
    }
    return text;
  }

  /**
   * Try to extract exact digital text from a PDF page (what you see if fonts render correctly).
   */
  function extractDigitalPage(textContent) {
    const { lines, fonts } = extractLinesFromTextContent(textContent);
    const rawText = lines.join('\n').trim();
    if (!rawText || rawText.length < 3) {
      return { success: false, reason: 'empty' };
    }

    const hasLegacy = [...fonts].some(isLegacyFontName)
      || (global.isLikelyPreeti && global.isLikelyPreeti(rawText));

    if (hasLegacy) {
      const converted = convertLegacyText(rawText, fonts);
      const ratio = devanagariRatio(converted);
      if (ratio >= 0.2 || converted !== rawText) {
        return {
          success: true,
          text: converted,
          method: 'digital_legacy',
          confidence: 100,
          charCount: converted.length,
        };
      }
    }

    const ratio = devanagariRatio(rawText);
    const hasUnicode = ratio >= 0.15;
    const hasLatin = /[a-zA-Z]{3,}/.test(rawText);

    if (hasUnicode || (hasLatin && rawText.length > 30)) {
      return {
        success: true,
        text: rawText,
        method: 'digital_unicode',
        confidence: 100,
        charCount: rawText.length,
      };
    }

    return { success: false, reason: 'not_digital', rawText };
  }

  global.PrecisionExtract = {
    extractDigitalPage,
    devanagariRatio,
    extractLinesFromTextContent,
  };
})(window);
