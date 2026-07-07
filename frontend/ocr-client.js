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

    // MAXIMUM QUALITY MODE - accuracy over speed
    // 300 pages may take 10-15 minutes. That's acceptable.
    const profiles = {
      high: {
        tier: 'high',
        maxWorkers: Math.min(4, Math.max(2, cores - 2)),
        jpegQuality: 0.98,
        sharpen: true,
        retryWeakPages: true,
        weakConfidence: 80,
        retryScaleBoost: 1.4,
        scales: { small: 3.5, medium: 3.2, large: 3.0, xlarge: 2.8 },
        docxScale: 3.0,
        label: 'Maximum quality (may take 10-15 min for large files)',
      },
      balanced: {
        tier: 'balanced',
        maxWorkers: Math.min(3, Math.max(2, cores - 1)),
        jpegQuality: 0.97,
        sharpen: true,
        retryWeakPages: true,
        weakConfidence: 75,
        retryScaleBoost: 1.35,
        scales: { small: 3.2, medium: 3.0, large: 2.8, xlarge: 2.6 },
        docxScale: 2.8,
        label: 'High quality mode',
      },
      safe: {
        tier: 'safe',
        maxWorkers: 2,
        jpegQuality: 0.96,
        sharpen: true,
        retryWeakPages: true,
        weakConfidence: 70,
        retryScaleBoost: 1.3,
        scales: { small: 3.0, medium: 2.8, large: 2.6, xlarge: 2.4 },
        docxScale: 2.5,
        label: 'Quality mode',
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

  function canvasToOcrImage(srcCanvas, profile) {
    if (global.ImageCompress) {
      const prepared = global.ImageCompress.prepareCanvasForOcr(srcCanvas, profile);
      const compressed = global.ImageCompress.compressCanvasForOcr(prepared, profile);
      return compressed.dataUrl;
    }
    return preprocessCanvas(srcCanvas, profile);
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
    return canvasToOcrImage(canvas, profile);
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

    return { text: ocr.text, confidence: ocr.confidence, method: 'image_ocr' };
  }

  async function extractPagePrecision(pdf, pageNum, scale, worker, profile) {
    const page = await pdf.getPage(pageNum);

    if (global.PrecisionExtract) {
      const textContent = await page.getTextContent();
      const digital = global.PrecisionExtract.extractDigitalPage(textContent);
      if (digital.success) {
        return {
          text: digital.text,
          confidence: 100,
          method: digital.method,
        };
      }
    }

    return ocrPageWithRetry(pdf, pageNum, scale, worker, profile);
  }

  async function extractPdf(file, lang, onProgress) {
    if (typeof pdfjsLib === 'undefined') throw new Error('PDF.js not loaded.');
    if (typeof Tesseract === 'undefined') throw new Error('Tesseract.js not loaded.');

    const sizeMb = file.size / (1024 * 1024);

    pdfjsLib.GlobalWorkerOptions.workerSrc =
      'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js';

    const pdf = await pdfjsLib.getDocument({
      data: await file.arrayBuffer(),
      disableFontFace: true,
      nativeImageDecoderSupport: 'none',
    }).promise;

    const totalPages = pdf.numPages;
    let profile = getProfile();
    if (global.ImageCompress) {
      profile = global.ImageCompress.profileForLargeFile(profile, sizeMb, totalPages);
    }
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

        results[pageNum - 1] = await extractPagePrecision(
          pdf,
          pageNum,
          scale,
          worker,
          profile,
        );
        done += 1;
        const method = results[pageNum - 1].method || 'image_ocr';
        const methodLabel = method.startsWith('digital') ? 'exact text' : 'OCR';
        if (onProgress) {
          onProgress(
            done,
            totalPages,
            `Page ${done}/${totalPages} · ${methodLabel} · ${profile.tier} mode`,
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
    const methods = results.map((r) => r.method || 'image_ocr');
    const digitalPages = methods.filter((m) => m.startsWith('digital')).length;
    const finalText =
      totalPages === 1 ? texts[0] : texts.join('\n\n--- Page Break ---\n\n');

    return {
      text: finalText,
      meta: {
        pages: totalPages,
        method: 'precision_hybrid',
        pipeline: 'digital_text_then_ocr_fallback',
        method_per_page: methods,
        digital_pages: digitalPages,
        ocr_pages: totalPages - digitalPages,
        file_size_mb: Math.round(sizeMb * 100) / 100,
        compressed_ocr: digitalPages < totalPages,
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
      const dataUrl = canvasToOcrImage(canvas, profile);
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
      const dataUrl = canvasToOcrImage(canvas, profile);
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

  function isImagePdfAvailable() {
    return typeof pdfjsLib !== 'undefined' && typeof PDFLib !== 'undefined';
  }

  const PDFJS_CMAP_URL = 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/cmaps/';
  const PDFJS_FONT_URL = 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/standard_fonts/';

  async function loadPdfDocumentForImages(file) {
    if (typeof pdfjsLib === 'undefined') {
      throw new Error('PDF.js not loaded. Check your internet connection and refresh.');
    }

    pdfjsLib.GlobalWorkerOptions.workerSrc =
      'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js';

    return pdfjsLib.getDocument({
      data: await file.arrayBuffer(),
      disableFontFace: false,
      standardFontDataUrl: PDFJS_FONT_URL,
      cMapUrl: PDFJS_CMAP_URL,
      cMapPacked: true,
    }).promise;
  }

  async function renderPdfPageCanvas(pdf, pageNum, renderScale) {
    const page = await pdf.getPage(pageNum);
    const baseViewport = page.getViewport({ scale: 1 });
    const viewport = page.getViewport({ scale: renderScale });
    const canvas = document.createElement('canvas');
    canvas.width = Math.floor(viewport.width);
    canvas.height = Math.floor(viewport.height);
    const ctx = canvas.getContext('2d', { alpha: false });
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    await page.render({
      canvasContext: ctx,
      viewport,
      intent: 'print',
    }).promise;
    return { canvas, baseViewport };
  }

  function canvasToJpegBytes(canvas, quality) {
    return new Promise((resolve, reject) => {
      canvas.toBlob(async (blob) => {
        if (!blob) {
          reject(new Error('Failed to encode page image.'));
          return;
        }
        resolve(new Uint8Array(await blob.arrayBuffer()));
      }, 'image/jpeg', quality);
    });
  }

  function imagePdfScaleForPages(pageCount) {
    if (pageCount > 150) return 3.5;
    if (pageCount > 50) return 4.0;
    return 4.5;
  }

  async function convertPdfToImagePdf(file, onProgress, options = {}) {
    if (typeof PDFLib === 'undefined') {
      throw new Error('PDF library not loaded. Check your internet connection and refresh.');
    }

    const jpegQuality = options.jpegQuality ?? 0.92;
    const pdf = await loadPdfDocumentForImages(file);
    const totalPages = pdf.numPages;
    const renderScale = options.renderScale ?? imagePdfScaleForPages(totalPages);
    const outputDoc = await PDFLib.PDFDocument.create();
    let totalJpegBytes = 0;

    for (let pageNum = 1; pageNum <= totalPages; pageNum += 1) {
      const { canvas, baseViewport } = await renderPdfPageCanvas(pdf, pageNum, renderScale);
      const jpegBytes = await canvasToJpegBytes(canvas, jpegQuality);
      totalJpegBytes += jpegBytes.length;

      if (jpegBytes.length < 1024) {
        throw new Error(`Page ${pageNum} did not render correctly. Try another browser.`);
      }

      const embedded = await outputDoc.embedJpg(jpegBytes);
      const widthPt = baseViewport.width;
      const heightPt = baseViewport.height;
      const page = outputDoc.addPage([widthPt, heightPt]);
      page.drawImage(embedded, {
        x: 0,
        y: 0,
        width: widthPt,
        height: heightPt,
      });

      if (onProgress) {
        onProgress(pageNum, totalPages, `Rendering page ${pageNum}/${totalPages}…`);
      }
    }

    const pdfBytes = await outputDoc.save();
    const blob = new Blob([pdfBytes], { type: 'application/pdf' });
    const dpi = Math.round(renderScale * 72);

    return {
      blob,
      meta: {
        pages: totalPages,
        dpi,
        render_scale: renderScale,
        jpeg_quality: jpegQuality,
        output_size_mb: Math.round((blob.size / (1024 * 1024)) * 100) / 100,
        input_size_mb: Math.round((file.size / (1024 * 1024)) * 100) / 100,
        embedded_image_bytes: totalJpegBytes,
        image_only_pdf: true,
        processed_locally: true,
      },
    };
  }

  async function convertDocxToImagePdf(file, onProgress, options = {}) {
    if (typeof docx === 'undefined' || typeof html2canvas === 'undefined') {
      throw new Error('DOCX libraries not loaded. Check your internet connection and refresh.');
    }
    if (typeof PDFLib === 'undefined') {
      throw new Error('PDF library not loaded. Check your internet connection and refresh.');
    }

    const jpegQuality = options.jpegQuality ?? 0.92;
    const renderScale = options.renderScale ?? 2.5;
    const container = document.createElement('div');
    container.style.cssText =
      'position:fixed;left:-10000px;top:0;width:794px;background:#fff;font-family:"Noto Sans Devanagari",sans-serif';
    document.body.appendChild(container);

    try {
      await docx.renderAsync(await file.arrayBuffer(), container, null, {
        inWrapper: true,
        breakPages: true,
      });

      let pageElements = container.querySelectorAll('section.docx');
      if (!pageElements.length) {
        pageElements = container.querySelectorAll('.docx-wrapper > section');
      }
      if (!pageElements.length) {
        pageElements = [container];
      }

      const outputDoc = await PDFLib.PDFDocument.create();
      const totalPages = pageElements.length;

      for (let index = 0; index < totalPages; index += 1) {
        const pageEl = pageElements[index];
        const canvas = await html2canvas(pageEl, {
          backgroundColor: '#ffffff',
          scale: renderScale,
          logging: false,
          useCORS: true,
        });
        const jpegBytes = await canvasToJpegBytes(canvas, jpegQuality);
        const embedded = await outputDoc.embedJpg(jpegBytes);

        const widthPt = (canvas.width / renderScale) * (72 / 96);
        const heightPt = (canvas.height / renderScale) * (72 / 96);
        const page = outputDoc.addPage([widthPt, heightPt]);
        page.drawImage(embedded, {
          x: 0,
          y: 0,
          width: widthPt,
          height: heightPt,
        });

        if (onProgress) {
          onProgress(index + 1, totalPages, `Rendering page ${index + 1}/${totalPages}…`);
        }
      }

      const pdfBytes = await outputDoc.save();
      const blob = new Blob([pdfBytes], { type: 'application/pdf' });
      return {
        blob,
        meta: {
          pages: totalPages,
          dpi: Math.round(renderScale * 96),
          render_scale: renderScale,
          jpeg_quality: jpegQuality,
          output_size_mb: Math.round((blob.size / (1024 * 1024)) * 100) / 100,
          input_size_mb: Math.round((file.size / (1024 * 1024)) * 100) / 100,
          image_only_pdf: true,
          processed_locally: true,
        },
      };
    } finally {
      document.body.removeChild(container);
    }
  }

  async function convertToImagePdf(file, onProgress, options = {}) {
    if (!isImagePdfAvailable()) {
      throw new Error('PDF conversion libraries not loaded. Check your internet connection and refresh.');
    }

    const ext = '.' + file.name.split('.').pop().toLowerCase();
    if (ext === '.pdf') {
      return convertPdfToImagePdf(file, onProgress, options);
    }
    if (ext === '.docx') {
      return convertDocxToImagePdf(file, onProgress, options);
    }
    throw new Error('Only PDF and DOCX files can be converted to image PDF.');
  }

  global.TextExtractOCR = {
    isAvailable,
    isImagePdfAvailable,
    getDeviceProfile: getProfile,
    extractPdf,
    extractImage,
    extractDocx,
    convertToImagePdf,
    convertPdfToImagePdf,
    convertDocxToImagePdf,
  };
})(window);
