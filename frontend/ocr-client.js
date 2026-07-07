/**
 * MAXIMUM ACCURACY OCR for Nepali text.
 * Optimized for speed without sacrificing accuracy.
 * Uses fast-path for easy pages, multi-pass only for difficult ones.
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

    const profiles = {
      high: {
        tier: 'high',
        maxWorkers: Math.min(4, Math.max(2, cores - 2)),
        scales: { small: 6.0, medium: 5.5, large: 5.0, xlarge: 4.5 },
        docxScale: 5.0,
        // Fast-path threshold - skip multi-pass if above this
        fastPathConfidence: 85,
        fastPathNepaliRatio: 0.3,
        // Retry settings for weak pages
        retryWeakPages: true,
        weakConfidence: 75,
        // Preprocessing
        contrast: 1.3,
        sharpenStrength: 1.5,
        label: 'High performance + accuracy',
      },
      balanced: {
        tier: 'balanced',
        maxWorkers: Math.min(3, Math.max(2, cores - 1)),
        scales: { small: 5.5, medium: 5.0, large: 4.5, xlarge: 4.0 },
        docxScale: 4.5,
        fastPathConfidence: 82,
        fastPathNepaliRatio: 0.25,
        retryWeakPages: true,
        weakConfidence: 70,
        contrast: 1.25,
        sharpenStrength: 1.3,
        label: 'Balanced speed + accuracy',
      },
      safe: {
        tier: 'safe',
        maxWorkers: 2,
        scales: { small: 5.0, medium: 4.5, large: 4.0, xlarge: 3.5 },
        docxScale: 4.0,
        fastPathConfidence: 80,
        fastPathNepaliRatio: 0.2,
        retryWeakPages: true,
        weakConfidence: 65,
        contrast: 1.2,
        sharpenStrength: 1.2,
        label: 'Standard accuracy',
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

  // ============== IMAGE PREPROCESSING ==============

  // Background removal: normalize paper to pure white, text to pure black
  function removeBackground(data, width, height) {
    const output = new Uint8ClampedArray(data.length);
    
    // Step 1: Find background color (brightest common color)
    // Sample pixels to build histogram
    const histogram = new Uint32Array(256);
    for (let i = 0; i < data.length; i += 4) {
      histogram[data[i]]++;
    }
    
    // Background is typically the most common bright value (> 180)
    let bgColor = 255;
    let maxCount = 0;
    for (let i = 180; i < 256; i++) {
      if (histogram[i] > maxCount) {
        maxCount = histogram[i];
        bgColor = i;
      }
    }
    
    // Step 2: Find text color (darkest common color)
    let textColor = 0;
    maxCount = 0;
    for (let i = 0; i < 100; i++) {
      if (histogram[i] > maxCount) {
        maxCount = histogram[i];
        textColor = i;
      }
    }
    
    // Step 3: Normalize - map background to 255, text to 0
    const range = Math.max(1, bgColor - textColor);
    
    for (let i = 0; i < data.length; i += 4) {
      const pixel = data[i];
      // Linear mapping with clipping
      let normalized = ((pixel - textColor) / range) * 255;
      normalized = Math.max(0, Math.min(255, Math.round(normalized)));
      
      // Extra push: make light pixels whiter, dark pixels blacker
      if (normalized > 200) normalized = 255;
      else if (normalized < 50) normalized = 0;
      
      output[i] = output[i + 1] = output[i + 2] = normalized;
      output[i + 3] = 255;
    }
    
    return output;
  }

  // Auto-deskew: detect and correct slight rotation
  function detectSkewAngle(data, width, height) {
    // Simple Hough-like approach: find dominant horizontal lines
    // For speed, sample every 4th row and look for dark pixel runs
    const angles = [];
    const sampleStep = 4;
    
    for (let y = height * 0.2; y < height * 0.8; y += sampleStep) {
      let runStart = -1;
      let runs = [];
      
      for (let x = 0; x < width; x++) {
        const pixel = data[(y * width + x) * 4];
        if (pixel < 128) { // Dark pixel
          if (runStart === -1) runStart = x;
        } else {
          if (runStart !== -1 && x - runStart > 20) {
            runs.push({ start: runStart, end: x - 1, y });
          }
          runStart = -1;
        }
      }
      
      // Calculate angles between runs on consecutive sampled rows
      if (runs.length > 0 && angles.length > 0) {
        const prevRuns = angles[angles.length - 1].runs;
        for (const r of runs) {
          for (const pr of prevRuns) {
            if (Math.abs(r.start - pr.start) < 50) {
              const dx = r.start - pr.start;
              const dy = sampleStep;
              const angle = Math.atan2(dx, dy) * 180 / Math.PI;
              if (Math.abs(angle) < 5) angles.push({ angle, y });
            }
          }
        }
      }
      angles.push({ runs, y });
    }
    
    // Return median angle (or 0 if not enough data)
    const validAngles = angles.filter(a => typeof a.angle === 'number').map(a => a.angle);
    if (validAngles.length < 5) return 0;
    validAngles.sort((a, b) => a - b);
    return validAngles[Math.floor(validAngles.length / 2)];
  }

  function applyDeskew(canvas, angle) {
    if (Math.abs(angle) < 0.1) return canvas; // Skip if nearly straight
    
    const w = canvas.width, h = canvas.height;
    const dst = document.createElement('canvas');
    // Expand canvas slightly to avoid clipping
    const rad = angle * Math.PI / 180;
    const sin = Math.abs(Math.sin(rad)), cos = Math.abs(Math.cos(rad));
    dst.width = Math.ceil(w * cos + h * sin);
    dst.height = Math.ceil(h * cos + w * sin);
    
    const ctx = dst.getContext('2d');
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(0, 0, dst.width, dst.height);
    ctx.translate(dst.width / 2, dst.height / 2);
    ctx.rotate(-rad);
    ctx.drawImage(canvas, -w / 2, -h / 2);
    
    return dst;
  }

  // Local adaptive thresholding (Sauvola-style) - optimized version
  function applyLocalAdaptiveThreshold(data, width, height, windowSize = 15, k = 0.2) {
    const output = new Uint8ClampedArray(data.length);
    const halfWindow = Math.floor(windowSize / 2);
    
    // Integral images for O(1) mean/variance calculation
    const integral = new Float64Array((width + 1) * (height + 1));
    const integralSq = new Float64Array((width + 1) * (height + 1));
    
    for (let y = 0; y < height; y++) {
      let rowSum = 0, rowSumSq = 0;
      for (let x = 0; x < width; x++) {
        const val = data[(y * width + x) * 4];
        rowSum += val;
        rowSumSq += val * val;
        const idx = (y + 1) * (width + 1) + (x + 1);
        integral[idx] = rowSum + integral[y * (width + 1) + (x + 1)];
        integralSq[idx] = rowSumSq + integralSq[y * (width + 1) + (x + 1)];
      }
    }
    
    for (let y = 0; y < height; y++) {
      for (let x = 0; x < width; x++) {
        const x1 = Math.max(0, x - halfWindow);
        const y1 = Math.max(0, y - halfWindow);
        const x2 = Math.min(width - 1, x + halfWindow);
        const y2 = Math.min(height - 1, y + halfWindow);
        const count = (x2 - x1 + 1) * (y2 - y1 + 1);
        
        const sum = integral[(y2 + 1) * (width + 1) + (x2 + 1)]
                  - integral[y1 * (width + 1) + (x2 + 1)]
                  - integral[(y2 + 1) * (width + 1) + x1]
                  + integral[y1 * (width + 1) + x1];
        const sumSq = integralSq[(y2 + 1) * (width + 1) + (x2 + 1)]
                    - integralSq[y1 * (width + 1) + (x2 + 1)]
                    - integralSq[(y2 + 1) * (width + 1) + x1]
                    + integralSq[y1 * (width + 1) + x1];
        
        const mean = sum / count;
        const std = Math.sqrt(Math.max(0, sumSq / count - mean * mean));
        const threshold = mean * (1 + k * (std / 128 - 1));
        
        const idx = (y * width + x) * 4;
        const pixel = data[idx];
        const newVal = pixel < threshold ? (pixel < threshold - 15 ? 0 : 40) : (pixel > threshold + 15 ? 255 : 215);
        output[idx] = output[idx + 1] = output[idx + 2] = newVal;
        output[idx + 3] = 255;
      }
    }
    return output;
  }

  // Fast median filter (optimized)
  function applyMedianFilter(data, width, height) {
    const output = new Uint8ClampedArray(data.length);
    const neighbors = new Uint8Array(9);
    
    for (let y = 1; y < height - 1; y++) {
      for (let x = 1; x < width - 1; x++) {
        let i = 0;
        for (let dy = -1; dy <= 1; dy++) {
          for (let dx = -1; dx <= 1; dx++) {
            neighbors[i++] = data[((y + dy) * width + (x + dx)) * 4];
          }
        }
        // Partial sort to find median (faster than full sort)
        for (let j = 0; j < 5; j++) {
          let minIdx = j;
          for (let k = j + 1; k < 9; k++) {
            if (neighbors[k] < neighbors[minIdx]) minIdx = k;
          }
          const tmp = neighbors[j]; neighbors[j] = neighbors[minIdx]; neighbors[minIdx] = tmp;
        }
        const idx = (y * width + x) * 4;
        output[idx] = output[idx + 1] = output[idx + 2] = neighbors[4];
        output[idx + 3] = 255;
      }
    }
    // Copy edges
    for (let x = 0; x < width; x++) {
      output[x * 4] = output[x * 4 + 1] = output[x * 4 + 2] = data[x * 4]; output[x * 4 + 3] = 255;
      const b = ((height - 1) * width + x) * 4;
      output[b] = output[b + 1] = output[b + 2] = data[b]; output[b + 3] = 255;
    }
    for (let y = 0; y < height; y++) {
      const l = y * width * 4;
      output[l] = output[l + 1] = output[l + 2] = data[l]; output[l + 3] = 255;
      const r = (y * width + width - 1) * 4;
      output[r] = output[r + 1] = output[r + 2] = data[r]; output[r + 3] = 255;
    }
    return output;
  }

  // Unsharp mask
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
        output[idx] = output[idx + 1] = output[idx + 2] = Math.max(0, Math.min(255, sum / kernelSum));
        output[idx + 3] = 255;
      }
    }
    // Copy edges
    for (let x = 0; x < width; x++) {
      output[x * 4] = data[x * 4]; output[x * 4 + 1] = data[x * 4]; output[x * 4 + 2] = data[x * 4]; output[x * 4 + 3] = 255;
      const b = ((height - 1) * width + x) * 4;
      output[b] = data[b]; output[b + 1] = data[b]; output[b + 2] = data[b]; output[b + 3] = 255;
    }
    for (let y = 0; y < height; y++) {
      const l = y * width * 4;
      output[l] = data[l]; output[l + 1] = data[l]; output[l + 2] = data[l]; output[l + 3] = 255;
      const r = (y * width + width - 1) * 4;
      output[r] = data[r]; output[r + 1] = data[r]; output[r + 2] = data[r]; output[r + 3] = 255;
    }
    return output;
  }

  // Morphological dilation
  function applyDilation(data, width, height) {
    const output = new Uint8ClampedArray(data.length);
    for (let y = 1; y < height - 1; y++) {
      for (let x = 1; x < width - 1; x++) {
        let minVal = 255;
        for (let dy = -1; dy <= 1; dy++) {
          for (let dx = -1; dx <= 1; dx++) {
            minVal = Math.min(minVal, data[((y + dy) * width + (x + dx)) * 4]);
          }
        }
        const idx = (y * width + x) * 4;
        output[idx] = output[idx + 1] = output[idx + 2] = minVal;
        output[idx + 3] = 255;
      }
    }
    // Copy edges
    for (let x = 0; x < width; x++) {
      output[x * 4] = data[x * 4]; output[x * 4 + 1] = data[x * 4]; output[x * 4 + 2] = data[x * 4]; output[x * 4 + 3] = 255;
      const b = ((height - 1) * width + x) * 4;
      output[b] = data[b]; output[b + 1] = data[b]; output[b + 2] = data[b]; output[b + 3] = 255;
    }
    for (let y = 0; y < height; y++) {
      const l = y * width * 4;
      output[l] = data[l]; output[l + 1] = data[l]; output[l + 2] = data[l]; output[l + 3] = 255;
      const r = (y * width + width - 1) * 4;
      output[r] = data[r]; output[r + 1] = data[r]; output[r + 2] = data[r]; output[r + 3] = 255;
    }
    return output;
  }

  // Preprocessing variants
  const PREPROCESS_VARIANTS = {
    default: { removeBg: true, contrast: 1.3, sharpen: 1.5, localThreshold: true, denoise: true, dilate: true },
    high_contrast: { removeBg: true, contrast: 1.5, sharpen: 1.5, localThreshold: true, denoise: true, dilate: true },
    extra_sharp: { removeBg: true, contrast: 1.3, sharpen: 2.0, localThreshold: true, denoise: false, dilate: true },
    sensitive: { removeBg: true, contrast: 1.2, sharpen: 1.3, localThreshold: true, denoise: true, dilate: false, thresholdK: 0.1 },
    clean: { removeBg: true, contrast: 1.4, sharpen: 1.8, localThreshold: true, denoise: true, dilate: true, deskew: true },
  };

  function preprocessCanvas(srcCanvas, variantName = 'default') {
    const variant = PREPROCESS_VARIANTS[variantName] || PREPROCESS_VARIANTS.default;
    let canvas = srcCanvas;
    
    // Step 0: Deskew if enabled
    if (variant.deskew) {
      const tempCanvas = document.createElement('canvas');
      tempCanvas.width = canvas.width;
      tempCanvas.height = canvas.height;
      const tempCtx = tempCanvas.getContext('2d');
      tempCtx.drawImage(canvas, 0, 0);
      const grayData = tempCtx.getImageData(0, 0, canvas.width, canvas.height);
      // Convert to grayscale for skew detection
      for (let i = 0; i < grayData.data.length; i += 4) {
        const g = 0.299 * grayData.data[i] + 0.587 * grayData.data[i + 1] + 0.114 * grayData.data[i + 2];
        grayData.data[i] = grayData.data[i + 1] = grayData.data[i + 2] = g;
      }
      const angle = detectSkewAngle(grayData.data, canvas.width, canvas.height);
      if (Math.abs(angle) > 0.3) {
        canvas = applyDeskew(canvas, angle);
      }
    }
    
    const w = canvas.width, h = canvas.height;
    const dst = document.createElement('canvas');
    dst.width = w; dst.height = h;
    const ctx = dst.getContext('2d', { willReadFrequently: true });
    ctx.drawImage(canvas, 0, 0);

    let imgData = ctx.getImageData(0, 0, w, h);
    let d = imgData.data;

    // Grayscale conversion first
    for (let i = 0; i < d.length; i += 4) {
      const g = Math.round(0.299 * d[i] + 0.587 * d[i + 1] + 0.114 * d[i + 2]);
      d[i] = d[i + 1] = d[i + 2] = g;
    }
    ctx.putImageData(imgData, 0, 0);

    // Step 1: Background removal - normalize paper to white, text to black
    if (variant.removeBg) {
      imgData = ctx.getImageData(0, 0, w, h);
      imgData.data.set(removeBackground(imgData.data, w, h));
      ctx.putImageData(imgData, 0, 0);
    }

    // Step 2: Contrast enhancement
    imgData = ctx.getImageData(0, 0, w, h);
    d = imgData.data;
    const contrast = variant.contrast;
    for (let i = 0; i < d.length; i += 4) {
      let g = d[i];
      g = Math.max(0, Math.min(255, (g - 128) * contrast + 128));
      d[i] = d[i + 1] = d[i + 2] = Math.round(g);
    }
    ctx.putImageData(imgData, 0, 0);

    // Denoise
    if (variant.denoise) {
      imgData = ctx.getImageData(0, 0, w, h);
      imgData.data.set(applyMedianFilter(imgData.data, w, h));
      ctx.putImageData(imgData, 0, 0);
    }

    // Sharpen
    if (variant.sharpen) {
      imgData = ctx.getImageData(0, 0, w, h);
      imgData.data.set(applyUnsharpMask(imgData.data, w, h, variant.sharpen));
      ctx.putImageData(imgData, 0, 0);
    }

    // Local adaptive threshold
    if (variant.localThreshold) {
      imgData = ctx.getImageData(0, 0, w, h);
      imgData.data.set(applyLocalAdaptiveThreshold(imgData.data, w, h, 15, variant.thresholdK || 0.2));
      ctx.putImageData(imgData, 0, 0);
    }

    // Dilation
    if (variant.dilate) {
      imgData = ctx.getImageData(0, 0, w, h);
      imgData.data.set(applyDilation(imgData.data, w, h));
      ctx.putImageData(imgData, 0, 0);
    }

    return dst.toDataURL('image/png');
  }

  // ============== TESSERACT WORKER POOL ==============

  class WorkerPool {
    constructor(lang, size, psm = 6) {
      this.lang = resolveLang(lang);
      this.size = size;
      this.psm = psm;
      this.workers = [];
      this.available = [];
      this.waiting = [];
      this.initialized = false;
    }

    async init() {
      if (this.initialized) return;
      for (let i = 0; i < this.size; i++) {
        const worker = await Tesseract.createWorker(this.lang, 1, {
          langPath: TESSDATA_URL,
          logger: () => {},
        });
        await worker.setParameters({
          tessedit_pageseg_mode: String(this.psm),
          preserve_interword_spaces: '1',
          textord_tabfind_find_tables: '1',
          textord_heavy_nr: '1',
          tessedit_do_invert: '0',
          textord_min_linesize: '2.0',
        });
        this.workers.push(worker);
        this.available.push(worker);
      }
      this.initialized = true;
    }

    acquire() {
      return new Promise((resolve) => {
        if (this.available.length > 0) {
          resolve(this.available.pop());
        } else {
          this.waiting.push(resolve);
        }
      });
    }

    release(worker) {
      if (this.waiting.length > 0) {
        const resolve = this.waiting.shift();
        resolve(worker);
      } else {
        this.available.push(worker);
      }
    }

    async terminate() {
      await Promise.all(this.workers.map(w => w.terminate().catch(() => {})));
      this.workers = [];
      this.available = [];
      this.initialized = false;
    }
  }

  // ============== OCR FUNCTIONS ==============

  function countNepaliChars(text) {
    return (text.match(/[\u0900-\u097F]/g) || []).length;
  }

  function extractTextFromWords(words) {
    if (!words || !words.length) return '';
    
    const cleanWords = words.filter(w => {
      const t = w.text.trim();
      if (!t || /^[_|\[\]\\\/=\-\.\,\s]+$/.test(t)) return false;
      if (/[\u0900-\u097F]/.test(t)) return w.confidence >= 15;
      return w.confidence >= 40 || t.length >= 3;
    });

    if (!cleanWords.length) return '';

    cleanWords.sort((a, b) => a.bbox.y0 - b.bbox.y0);
    const rows = [];
    let currentRow = [];

    for (const w of cleanWords) {
      if (!currentRow.length) { currentRow.push(w); continue; }
      const prev = currentRow[currentRow.length - 1];
      const avgHeight = (prev.bbox.y1 - prev.bbox.y0 + w.bbox.y1 - w.bbox.y0) / 2;
      if (Math.abs((w.bbox.y0 + w.bbox.y1) / 2 - (prev.bbox.y0 + prev.bbox.y1) / 2) < avgHeight * 0.6) {
        currentRow.push(w);
      } else {
        rows.push(currentRow);
        currentRow = [w];
      }
    }
    if (currentRow.length) rows.push(currentRow);

    return rows.map(row => {
      row.sort((a, b) => a.bbox.x0 - b.bbox.x0);
      let line = '';
      for (let i = 0; i < row.length; i++) {
        if (i === 0) { line = row[i].text; continue; }
        const prev = row[i - 1];
        const gap = row[i].bbox.x0 - prev.bbox.x1;
        const avgCharWidth = (prev.bbox.x1 - prev.bbox.x0) / Math.max(1, prev.text.length);
        line += gap > avgCharWidth * 4 ? '\t' : gap > avgCharWidth * 0.5 ? ' ' : '';
        line += row[i].text;
      }
      return line;
    }).join('\n').trim();
  }

  async function ocrWithWorker(worker, dataUrl) {
    const result = await worker.recognize(dataUrl);
    const text = extractTextFromWords(result.data.words) || (result.data.text || '').trim();
    return {
      text,
      confidence: Math.round(result.data.confidence || 0),
      nepaliChars: countNepaliChars(text),
    };
  }

  // Fast path: try default variant, return if good enough
  // Slow path: try additional variants only if fast path fails
  async function ocrPageAdaptive(canvas, workerPool, profile) {
    const worker = await workerPool.acquire();
    
    try {
      // FAST PATH: Default preprocessing
      const defaultUrl = preprocessCanvas(canvas, 'default');
      const defaultResult = await ocrWithWorker(worker, defaultUrl);
      
      const textLen = defaultResult.text.length;
      const nepaliRatio = textLen > 0 ? defaultResult.nepaliChars / textLen : 0;
      
      // Check if fast path is good enough
      if (defaultResult.confidence >= profile.fastPathConfidence && 
          (nepaliRatio >= profile.fastPathNepaliRatio || defaultResult.nepaliChars > 20)) {
        return { ...defaultResult, variant: 'default', fastPath: true };
      }

      // SLOW PATH: Try additional variants for difficult pages
      let best = { ...defaultResult, variant: 'default', fastPath: false };
      const slowVariants = ['clean', 'high_contrast', 'extra_sharp', 'sensitive'];
      
      for (const variantName of slowVariants) {
        const url = preprocessCanvas(canvas, variantName);
        const result = await ocrWithWorker(worker, url);
        
        // Score: prioritize Nepali content and text length over raw confidence
        const bestScore = best.confidence * 0.3 + best.nepaliChars * 2 + best.text.length * 0.1;
        const newScore = result.confidence * 0.3 + result.nepaliChars * 2 + result.text.length * 0.1;
        
        if (newScore > bestScore) {
          best = { ...result, variant: variantName, fastPath: false };
        }
        
        // Early exit if we found a great result
        if (result.confidence >= 90 && result.nepaliChars > 30) break;
      }
      
      return best;
    } finally {
      workerPool.release(worker);
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
    await page.render({ canvasContext: ctx, viewport, intent: 'print' }).promise;
    return canvas;
  }

  // ============== MAIN EXTRACTION ==============

  async function extractPdf(file, lang, onProgress) {
    if (typeof pdfjsLib === 'undefined') throw new Error('PDF.js not loaded.');
    if (typeof Tesseract === 'undefined') throw new Error('Tesseract.js not loaded.');

    const sizeMb = file.size / (1024 * 1024);
    pdfjsLib.GlobalWorkerOptions.workerSrc =
      'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js';

    const pdf = await pdfjsLib.getDocument({ data: await file.arrayBuffer(), disableFontFace: true }).promise;
    const totalPages = pdf.numPages;
    const profile = getProfile();
    const scale = scaleForPages(totalPages, profile);
    
    // Create worker pool
    const workerPool = new WorkerPool(lang, profile.maxWorkers);
    await workerPool.init();
    if (onProgress) onProgress(0, totalPages, 'OCR workers ready');

    const results = new Array(totalPages);
    let completed = 0;
    let fastPathCount = 0;

    // Process pages in parallel
    const pageQueue = Array.from({ length: totalPages }, (_, i) => i + 1);
    
    async function processPage(pageNum) {
      const canvas = await renderPdfPage(pdf, pageNum, scale);
      const result = await ocrPageAdaptive(canvas, workerPool, profile);
      results[pageNum - 1] = result;
      completed++;
      if (result.fastPath) fastPathCount++;
      if (onProgress) {
        const path = result.fastPath ? 'fast' : 'multi-pass';
        onProgress(completed, totalPages, `Page ${completed}/${totalPages} · ${path} · ${result.confidence}%`);
      }
    }

    // Run workers in parallel
    const workers = [];
    for (let i = 0; i < profile.maxWorkers; i++) {
      workers.push((async () => {
        while (pageQueue.length > 0) {
          const pageNum = pageQueue.shift();
          if (pageNum) await processPage(pageNum);
        }
      })());
    }
    await Promise.all(workers);
    await workerPool.terminate();

    const texts = results.map(r => r.text);
    const confidences = results.map(r => r.confidence);
    const nepaliCounts = results.map(r => r.nepaliChars);
    const finalText = totalPages === 1 ? texts[0] : texts.join('\n\n--- Page Break ---\n\n');

    return {
      text: finalText,
      meta: {
        pages: totalPages,
        method: 'adaptive_multipass_ocr',
        fast_path_pages: fastPathCount,
        slow_path_pages: totalPages - fastPathCount,
        file_size_mb: Math.round(sizeMb * 100) / 100,
        mean_confidence: Math.round((confidences.reduce((a, b) => a + b, 0) / confidences.length) * 10) / 10,
        min_confidence: Math.min(...confidences),
        max_confidence: Math.max(...confidences),
        total_nepali_chars: nepaliCounts.reduce((a, b) => a + b, 0),
        workers: profile.maxWorkers,
        render_scale: scale,
        device_tier: profile.tier,
        device_profile: profile.label,
        preprocessing: {
          background_removal: true,
          contrast: profile.contrast,
          sharpen: profile.sharpenStrength,
          local_adaptive_threshold: true,
          noise_reduction: true,
          morphological_cleanup: true,
          adaptive_multipass: true,
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

    const workerPool = new WorkerPool(lang, 1);
    await workerPool.init();
    const result = await ocrPageAdaptive(canvas, workerPool, profile);
    await workerPool.terminate();

    if (onProgress) onProgress(1, 1, 'Done');
    return {
      text: result.text,
      meta: {
        pages: 1,
        method: result.fastPath ? 'fast_path_ocr' : 'multipass_ocr',
        variant_used: result.variant,
        mean_confidence: result.confidence,
        nepali_chars: result.nepaliChars,
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
    if (onProgress) onProgress(0, 1, 'Rendering document…');

    const container = document.createElement('div');
    container.style.cssText = 'position:fixed;left:-10000px;top:0;width:900px;background:#fff;padding:48px;font-family:"Noto Sans Devanagari",sans-serif';
    document.body.appendChild(container);

    try {
      await docx.renderAsync(await file.arrayBuffer(), container, null, { inWrapper: true, breakPages: true });
      const canvas = await html2canvas(container, { backgroundColor: '#ffffff', scale: profile.docxScale, logging: false });

      if (onProgress) onProgress(0.5, 1, 'Running OCR…');
      const workerPool = new WorkerPool(lang, 1);
      await workerPool.init();
      const result = await ocrPageAdaptive(canvas, workerPool, profile);
      await workerPool.terminate();

      if (onProgress) onProgress(1, 1, 'Done');
      return {
        text: result.text,
        meta: {
          pages: 1,
          method: result.fastPath ? 'fast_path_ocr' : 'multipass_ocr',
          variant_used: result.variant,
          mean_confidence: result.confidence,
          nepali_chars: result.nepaliChars,
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

  function isAvailable() { return typeof Tesseract !== 'undefined'; }
  function isImagePdfAvailable() { return typeof pdfjsLib !== 'undefined' && typeof PDFLib !== 'undefined'; }

  async function loadPdfDocumentForImages(file) {
    if (typeof pdfjsLib === 'undefined') throw new Error('PDF.js not loaded.');
    pdfjsLib.GlobalWorkerOptions.workerSrc = 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js';
    return pdfjsLib.getDocument({
      data: await file.arrayBuffer(), disableFontFace: false,
      standardFontDataUrl: 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/standard_fonts/',
      cMapUrl: 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/cmaps/', cMapPacked: true,
    }).promise;
  }

  async function renderPdfPageCanvas(pdf, pageNum, renderScale) {
    const page = await pdf.getPage(pageNum);
    const baseViewport = page.getViewport({ scale: 1 });
    const viewport = page.getViewport({ scale: renderScale });
    const canvas = document.createElement('canvas');
    canvas.width = Math.floor(viewport.width); canvas.height = Math.floor(viewport.height);
    const ctx = canvas.getContext('2d', { alpha: false });
    ctx.fillStyle = '#ffffff'; ctx.fillRect(0, 0, canvas.width, canvas.height);
    await page.render({ canvasContext: ctx, viewport, intent: 'print' }).promise;
    return { canvas, baseViewport };
  }

  function canvasToJpegBytes(canvas, quality) {
    return new Promise((resolve, reject) => {
      canvas.toBlob(blob => blob ? blob.arrayBuffer().then(b => resolve(new Uint8Array(b))) : reject(new Error('Failed')), 'image/jpeg', quality);
    });
  }

  function imagePdfScaleForPages(pageCount) {
    return pageCount > 150 ? 3.5 : pageCount > 50 ? 4.0 : 4.5;
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
      if (onProgress) onProgress(pageNum, totalPages, `Rendering ${pageNum}/${totalPages}…`);
    }

    const pdfBytes = await outputDoc.save();
    const blob = new Blob([pdfBytes], { type: 'application/pdf' });
    return { blob, meta: { pages: totalPages, dpi: Math.round(renderScale * 72), output_size_mb: Math.round((blob.size / 1048576) * 100) / 100, input_size_mb: Math.round((file.size / 1048576) * 100) / 100, image_only_pdf: true, processed_locally: true } };
  }

  async function convertDocxToImagePdf(file, onProgress, options = {}) {
    if (typeof docx === 'undefined' || typeof html2canvas === 'undefined' || typeof PDFLib === 'undefined') throw new Error('Libraries not loaded.');
    const jpegQuality = options.jpegQuality ?? 0.92, renderScale = options.renderScale ?? 2.5;
    const container = document.createElement('div');
    container.style.cssText = 'position:fixed;left:-10000px;top:0;width:794px;background:#fff;font-family:"Noto Sans Devanagari",sans-serif';
    document.body.appendChild(container);
    try {
      await docx.renderAsync(await file.arrayBuffer(), container, null, { inWrapper: true, breakPages: true });
      let pages = container.querySelectorAll('section.docx');
      if (!pages.length) pages = container.querySelectorAll('.docx-wrapper > section');
      if (!pages.length) pages = [container];
      const outputDoc = await PDFLib.PDFDocument.create();
      for (let i = 0; i < pages.length; i++) {
        const canvas = await html2canvas(pages[i], { backgroundColor: '#ffffff', scale: renderScale, logging: false, useCORS: true });
        const jpegBytes = await canvasToJpegBytes(canvas, jpegQuality);
        const embedded = await outputDoc.embedJpg(jpegBytes);
        const w = (canvas.width / renderScale) * (72 / 96), h = (canvas.height / renderScale) * (72 / 96);
        outputDoc.addPage([w, h]).drawImage(embedded, { x: 0, y: 0, width: w, height: h });
        if (onProgress) onProgress(i + 1, pages.length, `Rendering ${i + 1}/${pages.length}…`);
      }
      const pdfBytes = await outputDoc.save();
      const blob = new Blob([pdfBytes], { type: 'application/pdf' });
      return { blob, meta: { pages: pages.length, dpi: Math.round(renderScale * 96), output_size_mb: Math.round((blob.size / 1048576) * 100) / 100, input_size_mb: Math.round((file.size / 1048576) * 100) / 100, image_only_pdf: true, processed_locally: true } };
    } finally { document.body.removeChild(container); }
  }

  async function convertToImagePdf(file, onProgress, options = {}) {
    if (!isImagePdfAvailable()) throw new Error('Libraries not loaded.');
    const ext = '.' + file.name.split('.').pop().toLowerCase();
    if (ext === '.pdf') return convertPdfToImagePdf(file, onProgress, options);
    if (ext === '.docx') return convertDocxToImagePdf(file, onProgress, options);
    throw new Error('Only PDF and DOCX supported.');
  }

  global.TextExtractOCR = {
    isAvailable, isImagePdfAvailable, getDeviceProfile: getProfile,
    extractPdf, extractImage, extractDocx,
    convertToImagePdf, convertPdfToImagePdf, convertDocxToImagePdf,
  };
})(window);
