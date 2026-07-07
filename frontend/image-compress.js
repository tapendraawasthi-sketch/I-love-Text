/**
 * Text-safe image compression for OCR pages only.
 * Adaptive JPEG quality — shrinks file size without losing text readability.
 */
(function (global) {
  'use strict';

  const DEFAULT_QUALITIES = [0.94, 0.90, 0.87, 0.84, 0.82];

  function estimateDataUrlBytes(dataUrl) {
    const base64 = dataUrl.split(',')[1] || '';
    return Math.floor((base64.length * 3) / 4);
  }

  /**
   * Grayscale + contrast (+ optional sharpen) — improves OCR on rendered pages.
   */
  function prepareCanvasForOcr(srcCanvas, profile) {
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

    return dst;
  }

  /**
   * Adaptive JPEG — pick highest quality that fits under maxBytesPerPage.
   */
  function compressCanvasForOcr(preparedCanvas, profile) {
    const maxBytes = profile.maxJpegBytesPerPage || 900000;
    const floor = profile.jpegQualityFloor || 0.82;
    const qualities = profile.jpegQualities || DEFAULT_QUALITIES;

    let best = null;
    for (const q of qualities) {
      if (q < floor) break;
      const dataUrl = preparedCanvas.toDataURL('image/jpeg', q);
      const bytes = estimateDataUrlBytes(dataUrl);
      best = { dataUrl, quality: q, bytes };
      if (bytes <= maxBytes) return best;
    }

    if (best) return best;
    const q = Math.max(floor, profile.jpegQuality || 0.88);
    const dataUrl = preparedCanvas.toDataURL('image/jpeg', q);
    return { dataUrl, quality: q, bytes: estimateDataUrlBytes(dataUrl) };
  }

  function profileForLargeFile(baseProfile, fileSizeMb, pageCount) {
    if (fileSizeMb <= 50 && pageCount <= 150) return baseProfile;
    return {
      ...baseProfile,
      maxJpegBytesPerPage: fileSizeMb > 70 ? 650000 : 750000,
      jpegQualityFloor: 0.82,
    };
  }

  global.ImageCompress = {
    prepareCanvasForOcr,
    compressCanvasForOcr,
    profileForLargeFile,
    estimateDataUrlBytes,
  };
})(window);
