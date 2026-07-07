/**
 * Device-aware browser OCR for Nepali text.
 * Detects CPU/RAM and only enables high-quality fast mode when supported.
 */
(function (global) {
  'use strict';

  const TESSDATA_URL = 'https://tessdata.projectnaptha.com/4.0.0_best';

  /**
   * Device tiers:
   * - high: 8+ cores & 8+ GB RAM (e.g. Acer Swift Go 14, 12 GB)
   * - balanced: 4+ cores & 4+ GB RAM
   * - safe: everything else
   */
  function detectDeviceProfile() {
    const cores = navigator.hardwareConcurrency || 4;
    const ramGb = navigator.deviceMemory || null;

    let tier = 'safe';
    if (cores >= 8 && (ramGb === null || ramGb >= 8)) {
      tier = 'high';
    } else if (cores >= 4 && (ramGb === null || ramGb >= 4)) {
      tier = 'balanced';
    }

    const profiles = {
      high: {
        tier: 'high',
        maxWorkers: Math.min(8, Math.max(6, cores - 4)),
        jpegQuality: 0.95,
        sharpen: true,
        retryWeakPages: true,
        weakConfidence: 65,
        retryScaleBoost: 1.2,
        scales: { small: 2.45, medium: 2.15, large: 2.0, xlarge: 1.85 },
        docxScale: 2.25,
        label: 'High performance (your device supports faster + sharper OCR)',
      },
      balanced: {
        tier: 'balanced',
        maxWorkers: Math.min(5, Math.max(3, cores - 2)),
        jpegQuality: 0.92,
        sharpen: true,
        retryWeakPages: false,
        weakConfidence: 60,
        retryScaleBoost: 1.1,
        scales: { small: 2.2, medium: 2.0, large: 1.85, xlarge: 1.7 },
        docxScale: 2.0,
        label: 'Balanced (matched to your device)',
      },
      safe: {
        tier: 'safe',
        maxWorkers: Math.min(2, cores),
        jpegQuality: 0.88,
        sharpen: false,
        retryWeakPages: false,
        weakConfidence: 55,
        retryScaleBoost: 1.0,
        scales: { small: 2.0, medium: 1.85, large: 1.7, xlarge: 1.55 },
        docxScale: 1.75,
        label: 'Safe mode (limited CPU/RAM detected)',
      },
    };

    const profile = profiles[tier];
    return {
      ...profile,
      cores,
      ramGb: ramGb ?? 'unknown',
      workers: profile.maxWorkers,
    };
  }

  let cachedProfile = null;

  function getProfile() {
    if (!cachedProfile) cachedProfile = detectDeviceProfile();
    return cachedProfile;
  }

  function resolveLang(lang) {
    if (lang === 'auto' || lang === 'nep') return 'nep+eng';
    if (lang === 'eng+nep') return 'nep+eng';
    return lang;
  }

  function scaleForPages(pageCount, profile) {
    const s = profile.scales;
    if (pageCount > 150) return s.xlarge;
    if (pageCount > 50) return s.large;
    if (pageCount > 20) return s.medium;
    return s.small;
  }

  function workerCount(pageCount, profile) {
    return Math.min(profile.maxWorkers, pageCount);
  }

  function wordsToTableText(result) {
    const cleanWords = (result.data.words || []).filter((w) => {
      const t = w.text.trim();
      if (/^[_|\[\]\\\/=\-]+$/.test(t)) return false;
      if (/[\u0900-\u097F]/.test(t) && w.confidence >= 28) return t.length > 0;
      if (w.confidence < 60 && /^[a-zA-Z&@#]+$/.test(t) && t.length < 4) return false;
      if (w.confidence < 40 && t.length < 3) return false;
      return t.length > 0;
    });

    cleanWords.sort((a, b) => a.bbox.y0 - b.bbox.y0);
    const rows = [];
    let currentRow = [];

    for (const w of cleanWords) {
      if (!currentRow.length) {
        currentRow.push(w);
        continue;
      }
      const prev = currentRow[currentRow.length - 1];
      const height = Math.max(1, prev.bbox.y1 - prev.bbox.y0);
      const cy1 = (w.bbox.y0 + w.bbox.y1) / 2;
      const cy2 = (prev.bbox.y0 + prev.bbox.y1) / 2;
      if (Math.abs(cy1 - cy2) < height * 0.6) currentRow.push(w);
      else {
        rows.push(currentRow);
        currentRow = [w];
      }
    }
    if (currentRow.length) rows.push(currentRow);

    return rows
      .map((row) => {
        row.sort((a, b) => a.bbox.x0 - b.bbox.x0);
        let line = '';
        for (let i = 0; i < row.length; i++) {
          if (i === 0) line += row[i].text;
          else {
            const prev = row[i - 1];
            const gap = row[i].bbox.x0 - prev.bbox.x1;
            const h = Math.max(1, prev.bbox.y1 - prev.bbox.y0);
            line += gap > h * 1.5 ? '\t' + row[i].text : ' ' + row[i].text;
          }
        }
        return line;
      })
      .join('\n')
      .trim() || (result.data.text || '').trim();
  }

  function preprocessCanvas(srcCanvas, profile) {
    const w = srcCanvas.width;
    const h = srcCanvas.height;
    const dst = document.createElement('canvas');
    dst.width = w;
    dst.height = h;
    const ctx = dst.getContext('2d', { willReadFrequently: true });
    ctx.drawImage(srcCanvas, 0, 0);

    const id = ctx.getImageData(0, 0, w, h);
    const d = id.data;
    const contrast = profile.tier === 'high' ? 1.18 : 1.12;

    for (let i = 0; i < d.length; i += 4) {
      let g = 0.299 * d[i] + 0.587 * d[i + 1] + 0.114 * d[i + 2];
      g = Math.max(0, Math.min(255, (g - 128) * contrast + 128));
      d[i] = d[i + 1] = d[i + 2] = g;
    }
    ctx.putImageData(id, 0, 0);

    if (profile.sharpen) {
      const sh = ctx.getImageData(0, 0, w, h);
      const src = new Uint8ClampedArray(sh.data);
      const k = [0, -1, 0, -1, 5, -1, 0, -1, 0];
      for (let y = 1; y < h - 1; y++) {
        for (let x = 1; x < w - 1; x++) {
          let r = 0;
          for (let ky = -1; ky <= 1; ky++) {
            for (let kx = -1; kx <= 1; kx++) {
              r += src[((y + ky) * w + (x + kx)) * 4] * k[(ky + 1) * 3 + (kx + 1)];
            }
          }
          const idx = (y * w + x) * 4;
          const val = Math.max(0, Math.min(255, r));
          sh.data[idx] = sh.data[idx + 1] = sh.data[idx + 2] = val;
        }
      }
      ctx.putImageData(sh, 0, 0);
    }

    return dst.toDataURL('image/jpeg', profile.jpegQuality);
  }

  async function createWorkers(lang, count, profile) {
    const tessLang = resolveLang(lang);
    const workers = [];
    for (let i = 0; i < count; i++) {
      const worker = await Tesseract.createWorker(tessLang, 1, {
        langPath: TESSDATA_URL,
        logger: () => {},
      });
      await worker.setParameters({
        tessedit_pageseg_mode: '3',
        preserve_interword_spaces: '1',
        textord_tabfind_find_tables: '1',
        textord_heavy_nr: profile.tier === 'high' ? '1' : '0',
      });
      workers.push(worker);
    }
    return workers;
  }

  async function terminateWorkers(workers) {
    await Promise.all(workers.map((w) => w.terminate().catch(() => {})));
  }

  async function renderPdfPage(pdf, pageNum, scale, profile) {
    const page = await pdf.getPage(pageNum);
    const viewport = page.getViewport({ scale });
    const canvas = document.createElement('canvas');
    canvas.width = viewport.width;
    canvas.height = viewport.height;
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    await page.render({ canvasContext: ctx, viewport }).promise;
    return preprocessCanvas(canvas, profile);
  }

  async function ocrDataUrl(worker, dataUrl) {
    const result = await worker.recognize(dataUrl);
    return {
      text: wordsToTableText(result),
      confidence: Math.round(result.data.confidence || 0),
      raw: result,
    };
  }

  async function ocrPageWithRetry(pdf, pageNum, scale, worker, profile) {
    const dataUrl = await renderPdfPage(pdf, pageNum, scale, profile);
    let ocr = await ocrDataUrl(worker, dataUrl);

    if (
      profile.retryWeakPages
      && ocr.confidence < profile.weakConfidence
      && scale * profile.retryScaleBoost <= 2.8
    ) {
      const retryUrl = await renderPdfPage(
        pdf,
        pageNum,
        scale * profile.retryScaleBoost,
        profile,
      );
      const retry = await ocrDataUrl(worker, retryUrl);
      if (retry.confidence > ocr.confidence || retry.text.length > ocr.text.length) {
        ocr = retry;
      }
    }

    return { text: ocr.text, confidence: ocr.confidence };
  }

  async function extractPdf(file, lang, onProgress) {
    if (typeof pdfjsLib === 'undefined') throw new Error('PDF.js not loaded.');
    if (typeof Tesseract === 'undefined') throw new Error('Tesseract.js not loaded.');

    const profile = getProfile();

    pdfjsLib.GlobalWorkerOptions.workerSrc =
      'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js';

    const pdf = await pdfjsLib.getDocument({
      data: await file.arrayBuffer(),
      disableFontFace: true,
      nativeImageDecoderSupport: 'none',
    }).promise;

    const totalPages = pdf.numPages;
    const scale = scaleForPages(totalPages, profile);
    const numWorkers = workerCount(totalPages, profile);
    const workers = await createWorkers(lang, numWorkers, profile);

    const pageQueue = Array.from({ length: totalPages }, (_, i) => i + 1);
    const results = new Array(totalPages);
    let done = 0;

    async function runWorker(worker) {
      while (pageQueue.length) {
        const pageNum = pageQueue.shift();
        if (!pageNum) break;

        results[pageNum - 1] = await ocrPageWithRetry(
          pdf,
          pageNum,
          scale,
          worker,
          profile,
        );
        done += 1;
        if (onProgress) {
          onProgress(
            done,
            totalPages,
            `Page ${done}/${totalPages} · ${profile.tier} mode · ${numWorkers} workers`,
          );
        }
      }
    }

    try {
      await Promise.all(workers.map((w) => runWorker(w)));
    } finally {
      await terminateWorkers(workers);
    }

    const texts = results.map((r) => r.text);
    const confidences = results.map((r) => r.confidence);
    const finalText =
      totalPages === 1 ? texts[0] : texts.join('\n\n--- Page Break ---\n\n');

    return {
      text: finalText,
      meta: {
        pages: totalPages,
        method: 'browser_parallel_ocr',
        pipeline: 'pdf_to_image_to_ocr_browser',
        method_per_page: Array(totalPages).fill('browser_ocr'),
        mean_confidence: confidences.length
          ? Math.round((confidences.reduce((a, b) => a + b, 0) / confidences.length) * 10) / 10
          : 0,
        workers: numWorkers,
        render_scale: scale,
        device_tier: profile.tier,
        device_cores: profile.cores,
        device_ram_gb: profile.ramGb,
        device_profile: profile.label,
        quality_retry: profile.retryWeakPages,
        processed_locally: true,
      },
    };
  }

  async function extractImage(file, lang, onProgress) {
    if (typeof Tesseract === 'undefined') throw new Error('Tesseract.js not loaded.');
    const profile = getProfile();
    if (onProgress) onProgress(0, 1, 'Processing image…');

    const workers = await createWorkers(lang, 1, profile);
    try {
      const bitmap = await createImageBitmap(file);
      const canvas = document.createElement('canvas');
      canvas.width = bitmap.width;
      canvas.height = bitmap.height;
      canvas.getContext('2d').drawImage(bitmap, 0, 0);
      const dataUrl = preprocessCanvas(canvas, profile);
      const ocr = await ocrDataUrl(workers[0], dataUrl);
      if (onProgress) onProgress(1, 1, 'Done');
      return {
        text: ocr.text,
        meta: {
          pages: 1,
          method: 'browser_ocr',
          mean_confidence: ocr.confidence,
          device_tier: profile.tier,
          device_profile: profile.label,
          processed_locally: true,
        },
      };
    } finally {
      await terminateWorkers(workers);
    }
  }

  async function extractDocx(file, lang, onProgress) {
    if (typeof docx === 'undefined' || typeof html2canvas === 'undefined') {
      throw new Error('DOCX libraries not loaded.');
    }
    const profile = getProfile();
    if (onProgress) onProgress(0, 1, 'Rendering Word as image…');

    const container = document.createElement('div');
    container.style.cssText =
      'position:fixed;left:-10000px;top:0;width:900px;background:#fff;padding:48px;font-family:"Noto Sans Devanagari",sans-serif';
    document.body.appendChild(container);

    try {
      await docx.renderAsync(await file.arrayBuffer(), container, null, {
        inWrapper: true,
        breakPages: true,
      });
      const canvas = await html2canvas(container, {
        backgroundColor: '#ffffff',
        scale: profile.docxScale,
        logging: false,
      });
      const dataUrl = preprocessCanvas(canvas, profile);
      const workers = await createWorkers(lang, 1, profile);
      try {
        const ocr = await ocrDataUrl(workers[0], dataUrl);
        if (onProgress) onProgress(1, 1, 'Done');
        return {
          text: ocr.text,
          meta: {
            pages: 1,
            method: 'browser_ocr',
            pipeline: 'docx_to_image_to_ocr_browser',
            mean_confidence: ocr.confidence,
            device_tier: profile.tier,
            device_profile: profile.label,
            processed_locally: true,
          },
        };
      } finally {
        await terminateWorkers(workers);
      }
    } finally {
      document.body.removeChild(container);
    }
  }

  function isAvailable() {
    return typeof Tesseract !== 'undefined';
  }

  global.TextExtractOCR = {
    isAvailable,
    getDeviceProfile: getProfile,
    extractPdf,
    extractImage,
    extractDocx,
  };
})(window);
