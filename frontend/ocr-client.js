/**
 * MAXIMUM ACCURACY OCR for Nepali text.
 * Pure image-based OCR - no text layer extraction.
 * Optimized for accuracy over speed.
 */
(function (global) {
  'use strict';

  const TESSDATA_URL = 'https://tessdata.projectnaptha.com/4.0.0_best';

  // ============== DEVICE DETECTION ==============

  function detectDeviceProfile() {
    const cores = navigator.hardwareConcurrency || 4;
    const ramGb = navigator.deviceMemory || null;

    let tier = 'safe';
    if (cores >= 8 && (ramGb === null || ramGb >= 8)) {
      tier = 'high';
    } else if (cores >= 4 && (ramGb === null || ramGb >= 4)) {
      tier = 'balanced';
    }

    // MAXIMUM QUALITY PROFILES - accuracy is everything
    const profiles = {
      high: {
        tier: 'high',
        maxWorkers: Math.min(3, Math.max(1, cores - 2)),
        // Very high render scales for maximum clarity
        scales: { small: 5.0, medium: 4.5, large: 4.0, xlarge: 3.5 },
        docxScale: 4.0,
        // Retry settings
        retryWeakPages: true,
        weakConfidence: 85,
        retryScaleBoost: 1.3,
        // Preprocessing
        contrast: 1.25,
        sharpenStrength: 1.2,
        adaptiveThreshold: true,
        noiseReduction: true,
        label: 'Maximum accuracy OCR',
      },
      balanced: {
        tier: 'balanced',
        maxWorkers: Math.min(2, cores - 1),
        scales: { small: 4.5, medium: 4.0, large: 3.5, xlarge: 3.2 },
        docxScale: 3.5,
        retryWeakPages: true,
        weakConfidence: 80,
        retryScaleBoost: 1.25,
        contrast: 1.22,
        sharpenStrength: 1.15,
        adaptiveThreshold: true,
        noiseReduction: true,
        label: 'High accuracy OCR',
      },
      safe: {
        tier: 'safe',
        maxWorkers: 1,
        scales: { small: 4.0, medium: 3.5, large: 3.2, xlarge: 3.0 },
        docxScale: 3.0,
        retryWeakPages: true,
        weakConfidence: 75,
        retryScaleBoost: 1.2,
        contrast: 1.2,
        sharpenStrength: 1.1,
        adaptiveThreshold: true,
        noiseReduction: false,
        label: 'Accuracy OCR',
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
    if (pageCount > 200) return s.xlarge;
    if (pageCount > 100) return s.large;
    if (pageCount > 30) return s.medium;
    return s.small;
  }

  function workerCount(pageCount, profile) {
    return Math.min(profile.maxWorkers, Math.max(1, pageCount));
  }

  // ============== ADVANCED IMAGE PREPROCESSING ==============

  function computeOtsuThreshold(grayData, width, height) {
    const histogram = new Array(256).fill(0);
    for (let i = 0; i < grayData.length; i += 4) {
      histogram[grayData[i]]++;
    }

    const total = width * height;
    let sum = 0;
    for (let i = 0; i < 256; i++) sum += i * histogram[i];

    let sumB = 0, wB = 0, wF = 0;
    let maxVariance = 0, threshold = 128;

    for (let t = 0; t < 256; t++) {
      wB += histogram[t];
      if (wB === 0) continue;
      wF = total - wB;
      if (wF === 0) break;

      sumB += t * histogram[t];
      const mB = sumB / wB;
      const mF = (sum - sumB) / wF;
      const variance = wB * wF * (mB - mF) * (mB - mF);

      if (variance > maxVariance) {
        maxVariance = variance;
        threshold = t;
      }
    }
    return threshold;
  }

  function applyMedianFilter(data, width, height) {
    const output = new Uint8ClampedArray(data.length);
    const getPixel = (x, y) => {
      x = Math.max(0, Math.min(width - 1, x));
      y = Math.max(0, Math.min(height - 1, y));
      return data[(y * width + x) * 4];
    };

    for (let y = 0; y < height; y++) {
      for (let x = 0; x < width; x++) {
        const neighbors = [];
        for (let dy = -1; dy <= 1; dy++) {
          for (let dx = -1; dx <= 1; dx++) {
            neighbors.push(getPixel(x + dx, y + dy));
          }
        }
        neighbors.sort((a, b) => a - b);
        const median = neighbors[4];
        const idx = (y * width + x) * 4;
        output[idx] = output[idx + 1] = output[idx + 2] = median;
        output[idx + 3] = 255;
      }
    }
    return output;
  }

  function applyUnsharpMask(data, width, height, strength) {
    const output = new Uint8ClampedArray(data.length);
    const kernel = [
      0, -1, 0,
      -1, 4 + strength, -1,
      0, -1, 0
    ];
    const kernelSum = strength;

    for (let y = 1; y < height - 1; y++) {
      for (let x = 1; x < width - 1; x++) {
        let sum = 0;
        for (let ky = -1; ky <= 1; ky++) {
          for (let kx = -1; kx <= 1; kx++) {
            const idx = ((y + ky) * width + (x + kx)) * 4;
            sum += data[idx] * kernel[(ky + 1) * 3 + (kx + 1)];
          }
        }
        const idx = (y * width + x) * 4;
        const val = Math.max(0, Math.min(255, sum / kernelSum));
        output[idx] = output[idx + 1] = output[idx + 2] = val;
        output[idx + 3] = 255;
      }
    }

    // Copy edges
    for (let x = 0; x < width; x++) {
      let idx = x * 4;
      output[idx] = output[idx + 1] = output[idx + 2] = data[idx];
      output[idx + 3] = 255;
      idx = ((height - 1) * width + x) * 4;
      output[idx] = output[idx + 1] = output[idx + 2] = data[idx];
      output[idx + 3] = 255;
    }
    for (let y = 0; y < height; y++) {
      let idx = y * width * 4;
      output[idx] = output[idx + 1] = output[idx + 2] = data[idx];
      output[idx + 3] = 255;
      idx = (y * width + width - 1) * 4;
      output[idx] = output[idx + 1] = output[idx + 2] = data[idx];
      output[idx + 3] = 255;
    }

    return output;
  }

  function preprocessCanvasForOcr(srcCanvas, profile) {
    const w = srcCanvas.width;
    const h = srcCanvas.height;
    const dst = document.createElement('canvas');
    dst.width = w;
    dst.height = h;
    const ctx = dst.getContext('2d', { willReadFrequently: true });
    ctx.drawImage(srcCanvas, 0, 0);

    let imgData = ctx.getImageData(0, 0, w, h);
    let d = imgData.data;

    // Step 1: Convert to grayscale with contrast enhancement
    const contrast = profile.contrast || 1.2;
    for (let i = 0; i < d.length; i += 4) {
      let g = 0.299 * d[i] + 0.587 * d[i + 1] + 0.114 * d[i + 2];
      g = Math.max(0, Math.min(255, (g - 128) * contrast + 128));
      d[i] = d[i + 1] = d[i + 2] = Math.round(g);
    }
    ctx.putImageData(imgData, 0, 0);

    // Step 2: Noise reduction (median filter)
    if (profile.noiseReduction) {
      imgData = ctx.getImageData(0, 0, w, h);
      const filtered = applyMedianFilter(imgData.data, w, h);
      imgData.data.set(filtered);
      ctx.putImageData(imgData, 0, 0);
    }

    // Step 3: Sharpening (unsharp mask)
    if (profile.sharpenStrength) {
      imgData = ctx.getImageData(0, 0, w, h);
      const sharpened = applyUnsharpMask(imgData.data, w, h, profile.sharpenStrength);
      imgData.data.set(sharpened);
      ctx.putImageData(imgData, 0, 0);
    }

    // Step 4: Adaptive binarization (Otsu threshold) - makes text crisp
    if (profile.adaptiveThreshold) {
      imgData = ctx.getImageData(0, 0, w, h);
      d = imgData.data;
      const threshold = computeOtsuThreshold(d, w, h);
      // Soft binarization - enhance contrast around threshold
      for (let i = 0; i < d.length; i += 4) {
        const g = d[i];
        let newVal;
        if (g < threshold - 30) {
          newVal = Math.max(0, g - 20); // Darken dark pixels
        } else if (g > threshold + 30) {
          newVal = Math.min(255, g + 20); // Lighten light pixels
        } else {
          // Sharpen transition zone
          newVal = g < threshold ? Math.max(0, g - 40) : Math.min(255, g + 40);
        }
        d[i] = d[i + 1] = d[i + 2] = newVal;
      }
      ctx.putImageData(imgData, 0, 0);
    }

    // Return as PNG (lossless) for maximum quality
    return dst.toDataURL('image/png');
  }

  // ============== TESSERACT WORKERS ==============

  async function createWorker(lang, profile, psm) {
    const tessLang = resolveLang(lang);
    const worker = await Tesseract.createWorker(tessLang, 1, {
      langPath: TESSDATA_URL,
      logger: () => {},
    });

    // Optimized Tesseract parameters for Nepali
    await worker.setParameters({
      tessedit_pageseg_mode: String(psm),
      preserve_interword_spaces: '1',
      textord_tabfind_find_tables: '1',
      textord_heavy_nr: '1',
      tessedit_do_invert: '0',
      textord_min_linesize: '2.5',
      edges_max_children_per_outline: '40',
      textord_noise_rejwords: '0',
      textord_noise_rejrows: '0',
      classify_bln_numeric_mode: '0',
    });

    return worker;
  }

  async function createWorkers(lang, count, profile) {
    const workers = [];
    for (let i = 0; i < count; i++) {
      // PSM 6 = Assume single uniform block of text (best for most docs)
      workers.push(await createWorker(lang, profile, 6));
    }
    return workers;
  }

  async function terminateWorkers(workers) {
    await Promise.all(workers.map((w) => w.terminate().catch(() => {})));
  }

  // ============== OCR PROCESSING ==============

  function extractTextFromResult(result) {
    // Use words for better layout reconstruction
    const words = result.data.words || [];
    if (!words.length) return (result.data.text || '').trim();

    // Filter out noise
    const cleanWords = words.filter((w) => {
      const t = w.text.trim();
      if (!t) return false;
      if (/^[_|\[\]\\\/=\-\.\,]+$/.test(t)) return false;
      // Keep Nepali text with lower confidence threshold
      if (/[\u0900-\u097F]/.test(t) && w.confidence >= 25) return true;
      // English requires higher confidence
      if (w.confidence < 50 && t.length < 3) return false;
      return true;
    });

    if (!cleanWords.length) return (result.data.text || '').trim();

    // Sort by Y position first (rows)
    cleanWords.sort((a, b) => a.bbox.y0 - b.bbox.y0);

    // Group into rows based on vertical position
    const rows = [];
    let currentRow = [];
    const lineHeightThreshold = 0.5;

    for (const w of cleanWords) {
      if (!currentRow.length) {
        currentRow.push(w);
        continue;
      }
      const prev = currentRow[currentRow.length - 1];
      const avgHeight = (prev.bbox.y1 - prev.bbox.y0 + w.bbox.y1 - w.bbox.y0) / 2;
      const centerY1 = (w.bbox.y0 + w.bbox.y1) / 2;
      const centerY2 = (prev.bbox.y0 + prev.bbox.y1) / 2;

      if (Math.abs(centerY1 - centerY2) < avgHeight * lineHeightThreshold) {
        currentRow.push(w);
      } else {
        rows.push(currentRow);
        currentRow = [w];
      }
    }
    if (currentRow.length) rows.push(currentRow);

    // Build text with proper spacing
    return rows
      .map((row) => {
        row.sort((a, b) => a.bbox.x0 - b.bbox.x0);
        let line = '';
        for (let i = 0; i < row.length; i++) {
          if (i === 0) {
            line = row[i].text;
          } else {
            const prev = row[i - 1];
            const gap = row[i].bbox.x0 - prev.bbox.x1;
            const avgCharWidth = (prev.bbox.x1 - prev.bbox.x0) / Math.max(1, prev.text.length);
            // Large gap = tab, small gap = space
            if (gap > avgCharWidth * 3) {
              line += '\t' + row[i].text;
            } else {
              line += ' ' + row[i].text;
            }
          }
        }
        return line;
      })
      .join('\n')
      .trim();
  }

  async function ocrDataUrl(worker, dataUrl) {
    const result = await worker.recognize(dataUrl);
    return {
      text: extractTextFromResult(result),
      confidence: Math.round(result.data.confidence || 0),
      words: result.data.words?.length || 0,
      raw: result,
    };
  }

  // Multi-pass OCR with different PSM modes
  async function ocrWithMultiplePsm(dataUrl, lang, profile) {
    // Try multiple page segmentation modes
    const psmModes = [6, 3, 4, 11]; // Block, Auto, Column, Sparse
    let bestResult = null;

    for (const psm of psmModes) {
      const worker = await createWorker(lang, profile, psm);
      try {
        const result = await worker.recognize(dataUrl);
        const text = extractTextFromResult(result);
        const confidence = Math.round(result.data.confidence || 0);
        const nepaliChars = (text.match(/[\u0900-\u097F]/g) || []).length;

        // Score based on confidence, text length, and Nepali content
        const score = confidence * 0.4 + Math.min(100, text.length / 10) * 0.3 + Math.min(100, nepaliChars) * 0.3;

        if (!bestResult || score > bestResult.score) {
          bestResult = { text, confidence, words: result.data.words?.length || 0, psm, score };
        }

        // If we got high confidence with good content, stop early
        if (confidence >= 85 && nepaliChars > 20) break;
      } finally {
        await worker.terminate().catch(() => {});
      }
    }

    return bestResult || { text: '', confidence: 0, words: 0, psm: 6, score: 0 };
  }

  async function renderPdfPage(pdf, pageNum, scale, profile) {
    const page = await pdf.getPage(pageNum);
    const viewport = page.getViewport({ scale });
    const canvas = document.createElement('canvas');
    canvas.width = Math.ceil(viewport.width);
    canvas.height = Math.ceil(viewport.height);
    const ctx = canvas.getContext('2d', { alpha: false });
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    await page.render({
      canvasContext: ctx,
      viewport,
      intent: 'print',
    }).promise;
    return preprocessCanvasForOcr(canvas, profile);
  }

  async function ocrPage(pdf, pageNum, scale, worker, profile) {
    const dataUrl = await renderPdfPage(pdf, pageNum, scale, profile);
    let ocr = await ocrDataUrl(worker, dataUrl);

    // Retry at higher scale if confidence is low
    if (profile.retryWeakPages && ocr.confidence < profile.weakConfidence) {
      const retryScale = Math.min(scale * profile.retryScaleBoost, 6.0);
      const retryUrl = await renderPdfPage(pdf, pageNum, retryScale, profile);
      const retry = await ocrDataUrl(worker, retryUrl);

      // Use retry if it's better
      if (retry.confidence > ocr.confidence || 
          (retry.confidence >= ocr.confidence - 5 && retry.text.length > ocr.text.length * 1.1)) {
        ocr = retry;
      }

      // If still weak, try multi-PSM approach
      if (ocr.confidence < profile.weakConfidence - 10) {
        const multiPsm = await ocrWithMultiplePsm(retryUrl, 'nep', profile);
        if (multiPsm.confidence > ocr.confidence || multiPsm.text.length > ocr.text.length * 1.2) {
          return { text: multiPsm.text, confidence: multiPsm.confidence, method: `image_ocr_psm${multiPsm.psm}` };
        }
      }
    }

    return { text: ocr.text, confidence: ocr.confidence, method: 'image_ocr' };
  }

  // ============== MAIN EXTRACTION FUNCTIONS ==============

  async function extractPdf(file, lang, onProgress) {
    if (typeof pdfjsLib === 'undefined') throw new Error('PDF.js not loaded.');
    if (typeof Tesseract === 'undefined') throw new Error('Tesseract.js not loaded.');

    const sizeMb = file.size / (1024 * 1024);

    pdfjsLib.GlobalWorkerOptions.workerSrc =
      'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js';

    const pdf = await pdfjsLib.getDocument({
      data: await file.arrayBuffer(),
      disableFontFace: true,
    }).promise;

    const totalPages = pdf.numPages;
    const profile = getProfile();
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

        results[pageNum - 1] = await ocrPage(pdf, pageNum, scale, worker, profile);
        done += 1;
        if (onProgress) {
          const conf = results[pageNum - 1].confidence;
          onProgress(
            done,
            totalPages,
            `Page ${done}/${totalPages} · OCR · conf ${conf}%`,
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
        method: 'pure_image_ocr',
        pipeline: 'pdf_render_preprocess_ocr',
        file_size_mb: Math.round(sizeMb * 100) / 100,
        mean_confidence: confidences.length
          ? Math.round((confidences.reduce((a, b) => a + b, 0) / confidences.length) * 10) / 10
          : 0,
        min_confidence: Math.min(...confidences),
        max_confidence: Math.max(...confidences),
        workers: numWorkers,
        render_scale: scale,
        device_tier: profile.tier,
        device_cores: profile.cores,
        device_ram_gb: profile.ramGb,
        device_profile: profile.label,
        preprocessing: {
          contrast: profile.contrast,
          sharpen: profile.sharpenStrength,
          adaptive_threshold: profile.adaptiveThreshold,
          noise_reduction: profile.noiseReduction,
        },
        processed_locally: true,
      },
    };
  }

  async function extractImage(file, lang, onProgress) {
    if (typeof Tesseract === 'undefined') throw new Error('Tesseract.js not loaded.');
    const profile = getProfile();
    if (onProgress) onProgress(0, 1, 'Processing image…');

    const bitmap = await createImageBitmap(file);
    const canvas = document.createElement('canvas');
    canvas.width = bitmap.width;
    canvas.height = bitmap.height;
    canvas.getContext('2d').drawImage(bitmap, 0, 0);
    const dataUrl = preprocessCanvasForOcr(canvas, profile);

    // Use multi-PSM for single images
    const ocr = await ocrWithMultiplePsm(dataUrl, lang, profile);
    if (onProgress) onProgress(1, 1, 'Done');

    return {
      text: ocr.text,
      meta: {
        pages: 1,
        method: 'pure_image_ocr',
        psm_used: ocr.psm,
        mean_confidence: ocr.confidence,
        device_tier: profile.tier,
        device_profile: profile.label,
        processed_locally: true,
      },
    };
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
      const dataUrl = preprocessCanvasForOcr(canvas, profile);
      const ocr = await ocrWithMultiplePsm(dataUrl, lang, profile);
      if (onProgress) onProgress(1, 1, 'Done');
      return {
        text: ocr.text,
        meta: {
          pages: 1,
          method: 'pure_image_ocr',
          pipeline: 'docx_to_image_to_ocr',
          psm_used: ocr.psm,
          mean_confidence: ocr.confidence,
          device_tier: profile.tier,
          device_profile: profile.label,
          processed_locally: true,
        },
      };
    } finally {
      document.body.removeChild(container);
    }
  }

  // ============== PDF CONVERSION (kept from before) ==============

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
      throw new Error('PDF.js not loaded.');
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
    await page.render({ canvasContext: ctx, viewport, intent: 'print' }).promise;
    return { canvas, baseViewport };
  }

  function canvasToJpegBytes(canvas, quality) {
    return new Promise((resolve, reject) => {
      canvas.toBlob(async (blob) => {
        if (!blob) { reject(new Error('Failed to encode.')); return; }
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
    if (typeof PDFLib === 'undefined') throw new Error('PDF library not loaded.');
    const jpegQuality = options.jpegQuality ?? 0.92;
    const pdf = await loadPdfDocumentForImages(file);
    const totalPages = pdf.numPages;
    const renderScale = options.renderScale ?? imagePdfScaleForPages(totalPages);
    const outputDoc = await PDFLib.PDFDocument.create();
    let totalJpegBytes = 0;

    for (let pageNum = 1; pageNum <= totalPages; pageNum++) {
      const { canvas, baseViewport } = await renderPdfPageCanvas(pdf, pageNum, renderScale);
      const jpegBytes = await canvasToJpegBytes(canvas, jpegQuality);
      totalJpegBytes += jpegBytes.length;
      const embedded = await outputDoc.embedJpg(jpegBytes);
      const page = outputDoc.addPage([baseViewport.width, baseViewport.height]);
      page.drawImage(embedded, { x: 0, y: 0, width: baseViewport.width, height: baseViewport.height });
      if (onProgress) onProgress(pageNum, totalPages, `Rendering page ${pageNum}/${totalPages}…`);
    }

    const pdfBytes = await outputDoc.save();
    const blob = new Blob([pdfBytes], { type: 'application/pdf' });
    return {
      blob,
      meta: {
        pages: totalPages,
        dpi: Math.round(renderScale * 72),
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
      throw new Error('DOCX libraries not loaded.');
    }
    if (typeof PDFLib === 'undefined') throw new Error('PDF library not loaded.');

    const jpegQuality = options.jpegQuality ?? 0.92;
    const renderScale = options.renderScale ?? 2.5;
    const container = document.createElement('div');
    container.style.cssText = 'position:fixed;left:-10000px;top:0;width:794px;background:#fff;font-family:"Noto Sans Devanagari",sans-serif';
    document.body.appendChild(container);

    try {
      await docx.renderAsync(await file.arrayBuffer(), container, null, { inWrapper: true, breakPages: true });
      let pageElements = container.querySelectorAll('section.docx');
      if (!pageElements.length) pageElements = container.querySelectorAll('.docx-wrapper > section');
      if (!pageElements.length) pageElements = [container];

      const outputDoc = await PDFLib.PDFDocument.create();
      const totalPages = pageElements.length;

      for (let index = 0; index < totalPages; index++) {
        const canvas = await html2canvas(pageElements[index], { backgroundColor: '#ffffff', scale: renderScale, logging: false, useCORS: true });
        const jpegBytes = await canvasToJpegBytes(canvas, jpegQuality);
        const embedded = await outputDoc.embedJpg(jpegBytes);
        const widthPt = (canvas.width / renderScale) * (72 / 96);
        const heightPt = (canvas.height / renderScale) * (72 / 96);
        const page = outputDoc.addPage([widthPt, heightPt]);
        page.drawImage(embedded, { x: 0, y: 0, width: widthPt, height: heightPt });
        if (onProgress) onProgress(index + 1, totalPages, `Rendering page ${index + 1}/${totalPages}…`);
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
    if (!isImagePdfAvailable()) throw new Error('PDF conversion libraries not loaded.');
    const ext = '.' + file.name.split('.').pop().toLowerCase();
    if (ext === '.pdf') return convertPdfToImagePdf(file, onProgress, options);
    if (ext === '.docx') return convertDocxToImagePdf(file, onProgress, options);
    throw new Error('Only PDF and DOCX files can be converted.');
  }

  // ============== EXPORTS ==============

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
