/**
 * Fast browser-side Nepali OCR — uses YOUR computer's CPU in parallel.
 * No server upload for PDF/DOCX/images = no timeout, no payment, no 502.
 */
(function (global) {
  'use strict';

  const TESSDATA_URL = 'https://tessdata.projectnaptha.com/4.0.0_best';

  function resolveLang(lang) {
    if (lang === 'auto' || lang === 'nep') return 'nep+eng';
    if (lang === 'eng+nep') return 'nep+eng';
    return lang;
  }

  function scaleForPages(pageCount) {
    if (pageCount > 150) return 1.65;
    if (pageCount > 50) return 1.85;
    if (pageCount > 20) return 2.0;
    return 2.2;
  }

  function workerCount(pageCount) {
    const cores = navigator.hardwareConcurrency || 4;
    return Math.min(Math.max(cores, 4), 16, pageCount);
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

  function preprocessCanvas(srcCanvas) {
    const w = srcCanvas.width;
    const h = srcCanvas.height;
    const dst = document.createElement('canvas');
    dst.width = w;
    dst.height = h;
    const ctx = dst.getContext('2d', { willReadFrequently: true });
    ctx.drawImage(srcCanvas, 0, 0);

    const id = ctx.getImageData(0, 0, w, h);
    const d = id.data;
    for (let i = 0; i < d.length; i += 4) {
      let g = 0.299 * d[i] + 0.587 * d[i + 1] + 0.114 * d[i + 2];
      g = Math.max(0, Math.min(255, (g - 128) * 1.12 + 128));
      d[i] = d[i + 1] = d[i + 2] = g;
    }
    ctx.putImageData(id, 0, 0);
    return dst.toDataURL('image/jpeg', 0.92);
  }

  async function createWorkers(lang, count) {
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
      });
      workers.push(worker);
    }
    return workers;
  }

  async function terminateWorkers(workers) {
    await Promise.all(workers.map((w) => w.terminate().catch(() => {})));
  }

  async function renderPdfPage(pdf, pageNum, scale) {
    const page = await pdf.getPage(pageNum);
    const viewport = page.getViewport({ scale });
    const canvas = document.createElement('canvas');
    canvas.width = viewport.width;
    canvas.height = viewport.height;
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    await page.render({ canvasContext: ctx, viewport }).promise;
    return preprocessCanvas(canvas);
  }

  async function ocrDataUrl(worker, dataUrl) {
    const result = await worker.recognize(dataUrl);
    return {
      text: wordsToTableText(result),
      confidence: Math.round(result.data.confidence || 0),
    };
  }

  async function extractPdf(file, lang, onProgress) {
    if (typeof pdfjsLib === 'undefined') throw new Error('PDF.js not loaded.');
    if (typeof Tesseract === 'undefined') throw new Error('Tesseract.js not loaded.');

    pdfjsLib.GlobalWorkerOptions.workerSrc =
      'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js';

    const pdf = await pdfjsLib.getDocument({
      data: await file.arrayBuffer(),
      disableFontFace: true,
      nativeImageDecoderSupport: 'none',
    }).promise;

    const totalPages = pdf.numPages;
    const scale = scaleForPages(totalPages);
    const numWorkers = workerCount(totalPages);
    const workers = await createWorkers(lang, numWorkers);

    const pageQueue = Array.from({ length: totalPages }, (_, i) => i + 1);
    const results = new Array(totalPages);
    let done = 0;

    async function runWorker(worker) {
      while (pageQueue.length) {
        const pageNum = pageQueue.shift();
        if (!pageNum) break;

        const dataUrl = await renderPdfPage(pdf, pageNum, scale);
        const ocr = await ocrDataUrl(worker, dataUrl);
        results[pageNum - 1] = ocr;
        done += 1;
        if (onProgress) {
          onProgress(done, totalPages, `Page ${done}/${totalPages} (browser OCR, ${numWorkers} workers)`);
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
      totalPages === 1 ? texts[0] : texts.map((t, i) => t).join('\n\n--- Page Break ---\n\n');

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
        processed_locally: true,
      },
    };
  }

  async function extractImage(file, lang, onProgress) {
    if (typeof Tesseract === 'undefined') throw new Error('Tesseract.js not loaded.');
    if (onProgress) onProgress(0, 1, 'Processing image…');
    const workers = await createWorkers(lang, 1);
    try {
      const dataUrl = await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result);
        reader.onerror = reject;
        reader.readAsDataURL(file);
      });
      const ocr = await ocrDataUrl(workers[0], dataUrl);
      if (onProgress) onProgress(1, 1, 'Done');
      return {
        text: ocr.text,
        meta: {
          pages: 1,
          method: 'browser_ocr',
          mean_confidence: ocr.confidence,
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
        scale: 2,
        logging: false,
      });
      const dataUrl = preprocessCanvas(canvas);
      const workers = await createWorkers(lang, 1);
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
    extractPdf,
    extractImage,
    extractDocx,
  };
})(window);
