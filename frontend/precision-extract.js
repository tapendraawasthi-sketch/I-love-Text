/**
 * Precision text extraction — digital text layer first, OCR only when needed.
 * 
 * For maximum Nepali accuracy:
 * - True Unicode PDFs (Mangal, Noto Sans Devanagari): extract digital text directly
 * - Legacy fonts (Preeti, Kantipur): fall back to OCR (reads rendered glyphs accurately)
 * - Scanned pages: OCR
 */
(function (global) {
  'use strict';

  const LEGACY_FONT_RE = /preeti|kantipur|sagarmatha|himali|pcs|aakriti|fontasy|ganesh|navjeevan|kanchan|siddhi|vishwash|ekantipur|himalb|annapurna|sabdatara|kunda|kanchi|padma|samman/i;
  const DEVANAGARI_RE = /[\u0900-\u097F]/;
  const GARBAGE_PATTERNS = /undefined|NaN|\[object|function\s*\(|\.{5,}|_{5,}/i;

  function devanagariRatio(text) {
    const letters = [...text].filter((c) => /\S/.test(c));
    if (!letters.length) return 0;
    return letters.filter((c) => DEVANAGARI_RE.test(c)).length / letters.length;
  }

  function isLegacyFontName(name) {
    return LEGACY_FONT_RE.test(name || '');
  }

  function hasGarbagePatterns(text) {
    return GARBAGE_PATTERNS.test(text);
  }

  function extractLinesFromTextContent(textContent) {
    const items = (textContent.items || []).filter((it) => it.str && it.str.trim());
    if (!items.length) return { lines: [], fonts: new Set(), hasLegacyFont: false };

    const fonts = new Set();
    let hasLegacyFont = false;

    const sorted = [...items].sort((a, b) => {
      const dy = b.transform[5] - a.transform[5];
      if (Math.abs(dy) > 2) return dy;
      return a.transform[4] - b.transform[4];
    });

    const rows = [];
    let current = [];
    let lastY = null;

    for (const item of sorted) {
      const fontName = item.fontName || '';
      fonts.add(fontName);
      if (isLegacyFontName(fontName)) {
        hasLegacyFont = true;
      }
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

    return { lines, fonts, hasLegacyFont };
  }

  /**
   * Check if raw text looks like Preeti-encoded (ASCII that represents Nepali).
   * Preeti text looks like random English letters: "g]kfn", ";/sf/", etc.
   */
  function looksLikeLegacyEncoded(text) {
    if (!text || text.length < 20) return false;
    
    // Common Preeti character sequences
    const preetiPatterns = [
      /[;:]/,           // Common in Preeti
      /[cfofdfg]/i,     // Common Preeti letter combos  
      /\|/,             // Pipe used in Preeti
      /[{}]/,           // Braces in some legacy fonts
    ];
    
    const devaRatio = devanagariRatio(text);
    const hasAsciiMix = /[a-zA-Z]/.test(text) && /[;:\|{}\[\]]/.test(text);
    
    // If very low Devanagari but lots of ASCII with special chars, likely legacy
    if (devaRatio < 0.1 && hasAsciiMix) {
      return true;
    }
    
    // Check for common Preeti indicators
    const indicators = ['cf', 'sf', 'of', 'df', 'jf', 'tf', 'xf', 'k|', 'sfo{', ';/sf/', 'g]kfn'];
    let score = 0;
    for (const ind of indicators) {
      if (text.includes(ind)) score++;
    }
    return score >= 2;
  }

  /**
   * Extract digital text from a PDF page.
   * Returns success:true only for clean Unicode text.
   * Legacy fonts and poor quality → success:false → triggers OCR fallback.
   */
  function extractDigitalPage(textContent) {
    const { lines, fonts, hasLegacyFont } = extractLinesFromTextContent(textContent);
    const rawText = lines.join('\n').trim();
    
    if (!rawText || rawText.length < 3) {
      return { success: false, reason: 'empty' };
    }

    // Check for garbage patterns (broken conversion)
    if (hasGarbagePatterns(rawText)) {
      return { success: false, reason: 'garbage_detected', rawText };
    }

    // If legacy font detected, use OCR for maximum accuracy
    // Legacy font text layers contain encoded ASCII, not readable text
    if (hasLegacyFont) {
      return { 
        success: false, 
        reason: 'legacy_font_use_ocr',
        rawText,
        detectedFonts: [...fonts].filter(isLegacyFontName),
      };
    }

    // Check if text looks like legacy-encoded even without font detection
    if (looksLikeLegacyEncoded(rawText)) {
      return { 
        success: false, 
        reason: 'likely_legacy_encoded',
        rawText,
      };
    }

    // Calculate Devanagari ratio for Unicode check
    const devaRatio = devanagariRatio(rawText);
    const hasLatin = /[a-zA-Z]{3,}/.test(rawText);
    const charCount = rawText.length;

    // Good Unicode Nepali text: high Devanagari ratio
    if (devaRatio >= 0.3) {
      return {
        success: true,
        text: rawText,
        method: 'digital_unicode',
        confidence: 100,
        charCount,
        devanagariRatio: Math.round(devaRatio * 100),
      };
    }

    // Mixed content (English + some Nepali) - accept if reasonable Devanagari
    if (devaRatio >= 0.1 && charCount > 50) {
      return {
        success: true,
        text: rawText,
        method: 'digital_mixed',
        confidence: 100,
        charCount,
        devanagariRatio: Math.round(devaRatio * 100),
      };
    }

    // Pure English text - accept
    if (hasLatin && devaRatio < 0.05 && charCount > 30) {
      return {
        success: true,
        text: rawText,
        method: 'digital_latin',
        confidence: 100,
        charCount,
      };
    }

    // Unclear - use OCR for safety
    return { 
      success: false, 
      reason: 'uncertain_encoding',
      rawText,
      devanagariRatio: Math.round(devaRatio * 100),
    };
  }

  global.PrecisionExtract = {
    extractDigitalPage,
    devanagariRatio,
    extractLinesFromTextContent,
    isLegacyFontName,
    looksLikeLegacyEncoded,
  };
})(window);
