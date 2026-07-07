/**
 * MAXIMUM ACCURACY OCR for Nepali text.
 * Pure image-based OCR with advanced preprocessing and multi-pass recognition.
 * Optimized for accuracy over speed - processing time is acceptable.
 */
(function (global) {
  'use strict';

  const TESSDATA_URL = 'https://tessdata.projectnaptha.com/4.0.0_best';

  // ============== DEVICE DETECTION ==============

  function detectDeviceProfile() {
    const cores = navigator.hardwareConcurrency || 4;
    const ramGb = navigator.deviceMemory || null;

    // Single profile - maximum accuracy for everyone
    // Processing time doesn't matter, only accuracy
    return {
      tier: 'maximum',
      maxWorkers: 1, // Single worker for stability
      // Very high render scales - 6× = ~430 DPI
      scales: { small: 6.0, medium: 5.5, large: 5.0, xlarge: 4.5 },
      docxScale: 5.0,
      // Aggressive retry
      retryWeakPages: true,
      weakConfidence: 88,
      retryScaleBoost: 1.2,
      // Preprocessing - maximum quality
      contrast: 1.3,
      sharpenStrength: 1.5,
      adaptiveThreshold: true,
      localAdaptive: true,
      noiseReduction: true,
      morphologicalCleanup: true,
      // Multi-pass OCR
      multiPassOcr: true,
      label: 'Maximum accuracy OCR',
      cores,
      ramGb: ramGb ?? 'unknown',
      workers: 1,
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

  // ============== ADVANCED IMAGE PREPROCESSING ==============

  // Compute histogram for threshold calculation
  function computeHistogram(data, width, height) {
    const histogram = new Uint32Array(256);
    for (let i = 0; i < data.length; i += 4) {
      histogram[data[i]]++;
    }
    return histogram;
  }

  // Otsu threshold for global reference
  function computeOtsuThreshold(histogram, total) {
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

  // Local adaptive thresholding (Sauvola-inspired)
  // Much better for text with varying backgrounds
  function applyLocalAdaptiveThreshold(data, width, height, windowSize = 15, k = 0.2) {
    const output = new Uint8ClampedArray(data.length);
    const halfWindow = Math.floor(windowSize / 2);
    
    // Create integral image and integral squared image for fast mean/std calculation
    const integral = new Float64Array((width + 1) * (height + 1));
    const integralSq = new Float64Array((width + 1) * (height + 1));
    
    for (let y = 0; y < height; y++) {
      let rowSum = 0, rowSumSq = 0;
      for (let x = 0; x < width; x++) {
        const idx = (y * width + x) * 4;
        const val = data[idx];
        rowSum += val;
        rowSumSq += val * val;
        
        const integralIdx = (y + 1) * (width + 1) + (x + 1);
        integral[integralIdx] = rowSum + integral[y * (width + 1) + (x + 1)];
        integralSq[integralIdx] = rowSumSq + integralSq[y * (width + 1) + (x + 1)];
      }
    }
    
    // Apply local threshold
    for (let y = 0; y < height; y++) {
      for (let x = 0; x < width; x++) {
        const x1 = Math.max(0, x - halfWindow);
        const y1 = Math.max(0, y - halfWindow);
        const x2 = Math.min(width - 1, x + halfWindow);
        const y2 = Math.min(height - 1, y + halfWindow);
        
        const count = (x2 - x1 + 1) * (y2 - y1 + 1);
        
        // Get sum and sum of squares from integral images
        const sum = integral[(y2 + 1) * (width + 1) + (x2 + 1)]
                  - integral[(y1) * (width + 1) + (x2 + 1)]
                  - integral[(y2 + 1) * (width + 1) + (x1)]
                  + integral[(y1) * (width + 1) + (x1)];
                  
        const sumSq = integralSq[(y2 + 1) * (width + 1) + (x2 + 1)]
                    - integralSq[(y1) * (width + 1) + (x2 + 1)]
                    - integralSq[(y2 + 1) * (width + 1) + (x1)]
                    + integralSq[(y1) * (width + 1) + (x1)];
        
        const mean = sum / count;
        const variance = (sumSq / count) - (mean * mean);
        const std = Math.sqrt(Math.max(0, variance));
        
        // Sauvola threshold: T = mean * (1 + k * (std / R - 1))
        // R is dynamic range (128 for 8-bit)
        const threshold = mean * (1 + k * (std / 128 - 1));
        
        const idx = (y * width + x) * 4;
        const pixel = data[idx];
        
        // Binarize with slight margin for anti-aliasing
        let newVal;
        if (pixel < threshold - 10) {
          newVal = 0; // Black (text)
        } else if (pixel > threshold + 10) {
          newVal = 255; // White (background)
        } else {
          // Transition zone - use gradient
          newVal = pixel < threshold ? 30 : 225;
        }
        
        output[idx] = output[idx + 1] = output[idx + 2] = newVal;
        output[idx + 3] = 255;
      }
    }
    
    return output;
  }

  // Median filter for noise reduction
  function applyMedianFilter(data, width, height) {
    const output = new Uint8ClampedArray(data.length);
    
    for (let y = 0; y < height; y++) {
      for (let x = 0; x < width; x++) {
        const neighbors = [];
        for (let dy = -1; dy <= 1; dy++) {
          for (let dx = -1; dx <= 1; dx++) {
            const nx = Math.max(0, Math.min(width - 1, x + dx));
            const ny = Math.max(0, Math.min(height - 1, y + dy));
            neighbors.push(data[(ny * width + nx) * 4]);
          }
        }
        neighbors.sort((a, b) => a - b);
        const idx = (y * width + x) * 4;
        output[idx] = output[idx + 1] = output[idx + 2] = neighbors[4];
        output[idx + 3] = 255;
      }
    }
    return output;
  }

  // Morphological dilation - connects broken character strokes
  function applyDilation(data, width, height, iterations = 1) {
    let current = new Uint8ClampedArray(data);
    
    for (let iter = 0; iter < iterations; iter++) {
      const output = new Uint8ClampedArray(current.length);
      
      for (let y = 0; y < height; y++) {
        for (let x = 0; x < width; x++) {
          let minVal = 255;
          // 3x3 structuring element
          for (let dy = -1; dy <= 1; dy++) {
            for (let dx = -1; dx <= 1; dx++) {
              const nx = Math.max(0, Math.min(width - 1, x + dx));
              const ny = Math.max(0, Math.min(height - 1, y + dy));
              minVal = Math.min(minVal, current[(ny * width + nx) * 4]);
            }
          }
          const idx = (y * width + x) * 4;
          output[idx] = output[idx + 1] = output[idx + 2] = minVal;
          output[idx + 3] = 255;
        }
      }
      current = output;
    }
    return current;
  }

  // Unsharp mask for sharpening
  function applyUnsharpMask(data, width, height, strength) {
    const output = new Uint8ClampedArray(data.length);
    const kernel = [0, -1, 0, -1, 4 + strength, -1, 0, -1, 0];
    const kernelSum = Math.max(1, strength);

    for (let y = 1; y < height - 1; y++) {
      for (let x = 1; x < width - 1; x++) {
        let sum = 0;
        for (let ky = -1; ky <= 1; ky++) {
          for (let kx = -1; kx <= 1; kx++) {
            sum += data[((y + ky) * width + (x + kx)) * 4] * kernel[(ky + 1) * 3 + (kx + 1)];
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
      output[x * 4] = output[x * 4 + 1] = output[x * 4 + 2] = data[x * 4];
      output[x * 4 + 3] = 255;
      const bottomIdx = ((height - 1) * width + x) * 4;
      output[bottomIdx] = output[bottomIdx + 1] = output[bottomIdx + 2] = data[bottomIdx];
      output[bottomIdx + 3] = 255;
    }
    for (let y = 0; y < height; y++) {
      const leftIdx = y * width * 4;
      output[leftIdx] = output[leftIdx + 1] = output[leftIdx + 2] = data[leftIdx];
      output[leftIdx + 3] = 255;
      const rightIdx = (y * width + width - 1) * 4;
      output[rightIdx] = output[rightIdx + 1] = output[rightIdx + 2] = data[rightIdx];
      output[rightIdx + 3] = 255;
    }

    return output;
  }

  // Main preprocessing pipeline
  function preprocessCanvasForOcr(srcCanvas, profile, variant = 'default') {
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
    const contrast = variant === 'high_contrast' ? 1.5 : (profile.contrast || 1.3);
    for (let i = 0; i < d.length; i += 4) {
      let g = 0.299 * d[i] + 0.587 * d[i + 1] + 0.114 * d[i + 2];
      g = Math.max(0, Math.min(255, (g - 128) * contrast + 128));
      d[i] = d[i + 1] = d[i + 2] = Math.round(g);
    }
    ctx.putImageData(imgData, 0, 0);

    // Step 2: Noise reduction
    if (profile.noiseReduction && variant !== 'no_denoise') {
      imgData = ctx.getImageData(0, 0, w, h);
      const filtered = applyMedianFilter(imgData.data, w, h);
      imgData.data.set(filtered);
      ctx.putImageData(imgData, 0, 0);
    }

    // Step 3: Sharpening
    if (profile.sharpenStrength && variant !== 'no_sharpen') {
      imgData = ctx.getImageData(0, 0, w, h);
      const strength = variant === 'extra_sharp' ? 2.0 : profile.sharpenStrength;
      const sharpened = applyUnsharpMask(imgData.data, w, h, strength);
      imgData.data.set(sharpened);
      ctx.putImageData(imgData, 0, 0);
    }

    // Step 4: Local adaptive thresholding (Sauvola-style)
    if (profile.localAdaptive && variant !== 'no_threshold') {
      imgData = ctx.getImageData(0, 0, w, h);
      const windowSize = variant === 'large_window' ? 25 : 15;
      const k = variant === 'sensitive' ? 0.1 : 0.2;
      const thresholded = applyLocalAdaptiveThreshold(imgData.data, w, h, windowSize, k);
      imgData.data.set(thresholded);
      ctx.putImageData(imgData, 0, 0);
    }

    // Step 5: Morphological cleanup - connect broken strokes
    if (profile.morphologicalCleanup && variant !== 'no_morph') {
      imgData = ctx.getImageData(0, 0, w, h);
      const dilated = applyDilation(imgData.data, w, h, 1);
      imgData.data.set(dilated);
      ctx.putImageData(imgData, 0, 0);
    }

    return dst.toDataURL('image/png');
  }

  // ============== TESSERACT WORKERS ==============

  async function createWorker(lang, psm, oem = 1) {
    const tessLang = resolveLang(lang);
    const worker = await Tesseract.createWorker(tessLang, oem, {
      langPath: TESSDATA_URL,
      logger: () => {},
    });

    await worker.setParameters({
      tessedit_pageseg_mode: String(psm),
      preserve_interword_spaces: '1',
      textord_tabfind_find_tables: '1',
      textord_heavy_nr: '1',
      tessedit_do_invert: '0',
      textord_min_linesize: '2.0',
      edges_max_children_per_outline: '50',
      textord_noise_rejwords: '0',
      textord_noise_rejrows: '0',
      classify_bln_numeric_mode: '0',
      tosp_min_sane_kn_sp: '1.5',
      textord_initialx_ile: '0.75',
      textord_initialasc_ile: '0.9',
    });

    return worker;
  }

  async function terminateWorker(worker) {
    try { await worker.terminate(); } catch (e) {}
  }

  // ============== OCR PROCESSING ==============

  function extractTextFromResult(result) {
    const words = result.data.words || [];
    if (!words.length) return (result.data.text || '').trim();

    // Filter words - be more lenient with Nepali
    const cleanWords = words.filter((w) => {
      const t = w.text.trim();
      if (!t) return false;
      if (/^[_|\[\]\\\/=\-\.\,\s]+$/.test(t)) return false;
      // Nepali text - accept with very low confidence (Nepali often scores low)
      if (/[\u0900-\u097F]/.test(t)) return w.confidence >= 15;
      // English needs higher confidence
      if (w.confidence < 40 && t.length < 3) return false;
      return true;
    });

    if (!cleanWords.length) return (result.data.text || '').trim();

    // Sort by Y position
    cleanWords.sort((a, b) => a.bbox.y0 - b.bbox.y0);

    // Group into rows
    const rows = [];
    let currentRow = [];

    for (const w of cleanWords) {
      if (!currentRow.length) {
        currentRow.push(w);
        continue;
      }
      const prev = currentRow[currentRow.length - 1];
      const avgHeight = (prev.bbox.y1 - prev.bbox.y0 + w.bbox.y1 - w.bbox.y0) / 2;
      const centerY1 = (w.bbox.y0 + w.bbox.y1) / 2;
      const centerY2 = (prev.bbox.y0 + prev.bbox.y1) / 2;

      if (Math.abs(centerY1 - centerY2) < avgHeight * 0.6) {
        currentRow.push(w);
      } else {
        rows.push(currentRow);
        currentRow = [w];
      }
    }
    if (currentRow.length) rows.push(currentRow);

    // Build text
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
            if (gap > avgCharWidth * 4) {
              line += '\t' + row[i].text;
            } else if (gap > avgCharWidth * 0.5) {
              line += ' ' + row[i].text;
            } else {
              line += row[i].text; // No space - likely connected
            }
          }
        }
        return line;
      })
      .join('\n')
      .trim();
  }

  // Count Nepali characters
  function countNepaliChars(text) {
    return (text.match(/[\u0900-\u097F]/g) || []).length;
  }

  // Score OCR result
  function scoreOcrResult(text, confidence) {
    const nepaliChars = countNepaliChars(text);
    const textLength = text.length;
    // Weighted score: confidence matters, but Nepali content and length also important
    return confidence * 0.3 + Math.min(100, textLength / 5) * 0.35 + Math.min(100, nepaliChars * 2) * 0.35;
  }

  async function ocrDataUrl(worker, dataUrl) {
    const result = await worker.recognize(dataUrl);
    const text = extractTextFromResult(result);
    return {
      text,
      confidence: Math.round(result.data.confidence || 0),
      words: result.data.words?.length || 0,
      nepaliChars: countNepaliChars(text),
      score: scoreOcrResult(text, result.data.confidence || 0),
    };
  }

  // Multi-pass OCR with different settings
  async function multiPassOcr(canvas, lang, profile) {
    const results = [];
    
    // Configuration variants to try
    const variants = [
      { preprocessing: 'default', psm: 6, name: 'default_psm6' },
      { preprocessing: 'high_contrast', psm: 6, name: 'highcontrast_psm6' },
      { preprocessing: 'default', psm: 3, name: 'default_psm3' },
      { preprocessing: 'extra_sharp', psm: 6, name: 'extrasharp_psm6' },
      { preprocessing: 'large_window', psm: 6, name: 'largewindow_psm6' },
      { preprocessing: 'sensitive', psm: 4, name: 'sensitive_psm4' },
    ];

    for (const variant of variants) {
      const dataUrl = preprocessCanvasForOcr(canvas, profile, variant.preprocessing);
      const worker = await createWorker(lang, variant.psm);
      try {
        const ocr = await ocrDataUrl(worker, dataUrl);
        results.push({ ...ocr, variant: variant.name });
        
        // If we got a very good result, we can stop early
        if (ocr.confidence >= 92 && ocr.nepaliChars >= 30) {
          break;
        }
      } finally {
        await terminateWorker(worker);
      }
    }

    // Find best result by score
    results.sort((a, b) => b.score - a.score);
    const best = results[0];
    
    // If multiple results have similar scores, merge them
    if (results.length > 1 && results[1].score > best.score * 0.9) {
      // Try to combine - use the one with more Nepali characters
      const bestNepali = results.reduce((a, b) => a.nepaliChars > b.nepaliChars ? a : b);
      if (bestNepali !== best && bestNepali.nepaliChars > best.nepaliChars * 1.2) {
        return bestNepali;
      }
    }

    return best;
  }

  // Single-pass OCR for pages (faster, used in retry logic)
  async function singlePassOcr(dataUrl, lang, psm = 6) {
    const worker = await createWorker(lang, psm);
    try {
      return await ocrDataUrl(worker, dataUrl);
    } finally {
      await terminateWorker(worker);
    }
  }

  async function renderPdfPage(pdf, pageNum, scale) {
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
    return canvas;
  }

  async function ocrPage(pdf, pageNum, scale, profile, lang) {
    const canvas = await renderPdfPage(pdf, pageNum, scale);
    
    // Use multi-pass OCR for maximum accuracy
    let ocr = await multiPassOcr(canvas, lang, profile);

    // Retry at higher scale if confidence is still low
    if (profile.retryWeakPages && ocr.confidence < profile.weakConfidence) {
      const retryScale = Math.min(scale * profile.retryScaleBoost, 7.0);
      const retryCanvas = await renderPdfPage(pdf, pageNum, retryScale);
      const retry = await multiPassOcr(retryCanvas, lang, profile);

      if (retry.score > ocr.score) {
        ocr = retry;
      }
    }

    return {
      text: ocr.text,
      confidence: ocr.confidence,
      nepaliChars: ocr.nepaliChars,
      variant: ocr.variant,
      method: 'multi_pass_ocr',
    };
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
    
    const results = [];

    for (let pageNum = 1; pageNum <= totalPages; pageNum++) {
      if (onProgress) {
        onProgress(pageNum - 1, totalPages, `Processing page ${pageNum}/${totalPages}...`);
      }
      
      const result = await ocrPage(pdf, pageNum, scale, profile, lang);
      results.push(result);
      
      if (onProgress) {
        onProgress(pageNum, totalPages, `Page ${pageNum}/${totalPages} done · conf ${result.confidence}%`);
      }
    }

    const texts = results.map((r) => r.text);
    const confidences = results.map((r) => r.confidence);
    const nepaliCounts = results.map((r) => r.nepaliChars);
    const finalText = totalPages === 1 ? texts[0] : texts.join('\n\n--- Page Break ---\n\n');

    return {
      text: finalText,
      meta: {
        pages: totalPages,
        method: 'multi_pass_ocr',
        pipeline: 'pdf_render_multipass_ocr',
        file_size_mb: Math.round(sizeMb * 100) / 100,
        mean_confidence: confidences.length
          ? Math.round((confidences.reduce((a, b) => a + b, 0) / confidences.length) * 10) / 10
          : 0,
        min_confidence: Math.min(...confidences),
        max_confidence: Math.max(...confidences),
        total_nepali_chars: nepaliCounts.reduce((a, b) => a + b, 0),
        render_scale: scale,
        device_tier: profile.tier,
        device_profile: profile.label,
        preprocessing: {
          contrast: profile.contrast,
          sharpen: profile.sharpenStrength,
          local_adaptive_threshold: profile.localAdaptive,
          noise_reduction: profile.noiseReduction,
          morphological_cleanup: profile.morphologicalCleanup,
          multi_pass: profile.multiPassOcr,
        },
        processed_locally: true,
      },
    };
  }

  async function extractImage(file, lang, onProgress) {
    if (typeof Tesseract === 'undefined') throw new Error('Tesseract.js not loaded.');
    const profile = getProfile();
    if (onProgress) onProgress(0, 1, 'Processing image with multi-pass OCR…');

    const bitmap = await createImageBitmap(file);
    const canvas = document.createElement('canvas');
    canvas.width = bitmap.width;
    canvas.height = bitmap.height;
    canvas.getContext('2d').drawImage(bitmap, 0, 0);
    
    const ocr = await multiPassOcr(canvas, lang, profile);
    if (onProgress) onProgress(1, 1, 'Done');

    return {
      text: ocr.text,
      meta: {
        pages: 1,
        method: 'multi_pass_ocr',
        variant_used: ocr.variant,
        mean_confidence: ocr.confidence,
        nepali_chars: ocr.nepaliChars,
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
    if (onProgress) onProgress(0, 1, 'Rendering Word document…');

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
      
      if (onProgress) onProgress(0.5, 1, 'Running multi-pass OCR…');
      const ocr = await multiPassOcr(canvas, lang, profile);
      if (onProgress) onProgress(1, 1, 'Done');
      
      return {
        text: ocr.text,
        meta: {
          pages: 1,
          method: 'multi_pass_ocr',
          pipeline: 'docx_to_image_to_multipass_ocr',
          variant_used: ocr.variant,
          mean_confidence: ocr.confidence,
          nepali_chars: ocr.nepaliChars,
          device_tier: profile.tier,
          device_profile: profile.label,
          processed_locally: true,
        },
      };
    } finally {
      document.body.removeChild(container);
    }
  }

  // ============== PDF CONVERSION (unchanged) ==============

  function isAvailable() {
    return typeof Tesseract !== 'undefined';
  }

  function isImagePdfAvailable() {
    return typeof pdfjsLib !== 'undefined' && typeof PDFLib !== 'undefined';
  }

  async function loadPdfDocumentForImages(file) {
    if (typeof pdfjsLib === 'undefined') throw new Error('PDF.js not loaded.');
    pdfjsLib.GlobalWorkerOptions.workerSrc =
      'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js';
    return pdfjsLib.getDocument({
      data: await file.arrayBuffer(),
      disableFontFace: false,
      standardFontDataUrl: 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/standard_fonts/',
      cMapUrl: 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/cmaps/',
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
