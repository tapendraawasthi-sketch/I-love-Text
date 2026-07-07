/* ============================================================
   ZipScan Pro v3 — Multi-Format OCR App
   Supports: ZIP · PDF · Word · Images · TXT · CSV
   ============================================================ */
'use strict';

// ─── State ────────────────────────────────────────────────────────
const state = {
  results: [],
  cancelled: false,
  startTime: null,
  timerInterval: null,
  activeWorkers: [],
  currentView: 'grid',
  filterMode: 'all',
  searchQuery: '',
  currentMode: 'auto',
};

// ─── DOM Refs ─────────────────────────────────────────────────────
const $ = id => document.getElementById(id);
const dropZone          = $('dropZone');
const fileInput         = $('fileInput');
const browseBtn         = $('browseBtn');
const processingSection = $('processingSection');
const resultsSection    = $('resultsSection');
const processingTitle   = $('processingTitle');
const processingModeBadge = $('processingModeBadge');
const progressLabel     = $('progressLabel');
const progressPct       = $('progressPct');
const progressFill      = $('progressFill');
const statTotal         = $('statTotal');
const statProcessed     = $('statProcessed');
const statFailed        = $('statFailed');
const statWords         = $('statWords');
const statTime          = $('statTime');
const liveQueue         = $('liveQueue');
const cancelBtn         = $('cancelBtn');
const resultsGrid       = $('resultsGrid');
const resultsMeta       = $('resultsMeta');
const searchInput       = $('searchInput');
const previewModal      = $('previewModal');
const modalClose        = $('modalClose');
const modalImage        = $('modalImage');
const modalTextarea     = $('modalTextarea');
const modalTitle        = $('modalTitle');
const modalTypeBadge    = $('modalTypeBadge');
const modalConfidence   = $('modalConfidence');
const modalBody         = $('modalBody');
const modalImagePanel   = $('modalImagePanel');
const toastContainer    = $('toastContainer');

// ─── Settings ─────────────────────────────────────────────────────
const getSettings = () => ({
  lang: $('ocrLang').value,
  psm: parseInt($('ocrMode').value, 10),
  quality: $('imgQuality').value,
  pdfScale: parseFloat($('pdfScale').value),
  preprocess: $('togglePreprocess').checked,
  concurrent: $('toggleConcurrent').checked,
});

// ─── File type detection ──────────────────────────────────────────
const IMAGE_EXTS = new Set(['jpg','jpeg','png','bmp','tif','tiff','webp','gif','pbm','pgm','ppm','jfif','heic','heif']);
const isImage = name => IMAGE_EXTS.has(name.split('.').pop().toLowerCase());
const getExt  = name => name.split('.').pop().toLowerCase();

const FILE_TYPES = {
  zip:   { exts: ['zip'], icon: '📦', label: 'ZIP', color: '#FCD34D', badgeClass: 'type-zip' },
  pdf:   { exts: ['pdf'], icon: '📄', label: 'PDF', color: '#FCA5A5', badgeClass: 'type-pdf' },
  word:  { exts: ['docx','doc'], icon: '📝', label: 'WORD', color: '#A5B4FC', badgeClass: 'type-word' },
  excel: { exts: ['xlsx','xls'], icon: '📊', label: 'EXCEL', color: '#34D399', badgeClass: 'type-excel' },
  text:  { exts: ['txt','csv','md'], icon: '📃', label: 'TEXT', color: '#9CA3AF', badgeClass: 'type-text' },
  image: { exts: [...IMAGE_EXTS], icon: '🖼️', label: 'IMAGE', color: '#67E8F9', badgeClass: 'type-image' },
};

function detectFileType(name) {
  const ext = getExt(name);
  for (const [type, info] of Object.entries(FILE_TYPES)) {
    if (info.exts.includes(ext)) return type;
  }
  return 'unknown';
}

// ─── Mode Tabs ────────────────────────────────────────────────────
const modeConfig = {
  auto:   { accept: '.zip,.pdf,.docx,.doc,.xlsx,.xls,.txt,.csv,.jpg,.jpeg,.png,.bmp,.tif,.tiff,.webp,.gif,.heic,.heif', multiple: true, title: 'Drop any file here', subtitle: 'ZIP, PDF, Word, Excel, Images, TXT — up to <strong>500 MB</strong>', icon: '✨' },
  zip:    { accept: '.zip', multiple: false, title: 'Drop your ZIP archive', subtitle: 'Extracts & OCRs all images inside — up to <strong>500 MB</strong>', icon: '📦' },
  pdf:    { accept: '.pdf', multiple: true, title: 'Drop PDF file(s)', subtitle: 'Renders each page & extracts text — up to <strong>200 MB</strong>', icon: '📄' },
  word:   { accept: '.docx,.doc', multiple: true, title: 'Drop Word document(s)', subtitle: 'Extracts text directly from .docx/.doc files', icon: '📝' },
  images: { accept: '.jpg,.jpeg,.png,.bmp,.tif,.tiff,.webp,.gif,.heic,.heif', multiple: true, title: 'Drop image file(s)', subtitle: 'OCR all selected images — multi-select supported', icon: '🖼️' },
  text:   { accept: '.txt,.csv,.md,.xlsx,.xls', multiple: true, title: 'Drop Data/Text file(s)', subtitle: 'Extracts spreadsheets and text files', icon: '📃' },
};

document.querySelectorAll('.mode-tab').forEach(tab => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.mode-tab').forEach(t => t.classList.remove('active'));
    tab.classList.add('active');
    const mode = tab.dataset.mode;
    state.currentMode = mode;
    const cfg = modeConfig[mode];
    fileInput.accept = cfg.accept;
    fileInput.multiple = cfg.multiple;
    $('uploadTitle').textContent = cfg.title;
    $('uploadSubtitle').innerHTML = cfg.subtitle;
    $('uploadIconDisplay').innerHTML = `<span style="font-size:3.5rem">${cfg.icon}</span>`;
  });
});

// ─── Particles ────────────────────────────────────────────────────
function initParticles() {
  const c = $('bgParticles');
  const colors = ['#6366F1','#06B6D4','#A78BFA','#10B981'];
  for (let i = 0; i < 28; i++) {
    const p = document.createElement('div');
    p.className = 'particle';
    const sz = Math.random()*4+1, dur = Math.random()*20+15, del = Math.random()*15;
    p.style.cssText = `width:${sz}px;height:${sz}px;left:${Math.random()*100}%;background:${colors[i%4]};animation-duration:${dur}s;animation-delay:${del}s;`;
    c.appendChild(p);
  }
}
initParticles();

// ─── File Input / Drop ────────────────────────────────────────────
browseBtn.addEventListener('click', () => fileInput.click());
dropZone.addEventListener('click', e => { if (!e.target.closest('.btn-upload')) fileInput.click(); });
fileInput.addEventListener('change', e => { if (e.target.files.length) handleFiles(Array.from(e.target.files)); });
dropZone.addEventListener('dragover', e => { e.preventDefault(); dropZone.classList.add('drag-over'); });
dropZone.addEventListener('dragleave', () => dropZone.classList.remove('drag-over'));
dropZone.addEventListener('drop', e => {
  e.preventDefault(); dropZone.classList.remove('drag-over');
  const files = Array.from(e.dataTransfer.files);
  if (files.length) handleFiles(files);
});

// ─── Route Files ──────────────────────────────────────────────────
async function handleFiles(files) {
  const MAX = 500 * 1024 * 1024;
  for (const f of files) {
    if (f.size > MAX) { showToast(`${f.name} exceeds 500 MB limit.`, 'error'); return; }
  }
  resetState();
  processingSection.classList.remove('hidden');
  resultsSection.classList.add('hidden');

  // Group by type
  const zips = files.filter(f => getExt(f.name) === 'zip');
  const pdfs = files.filter(f => getExt(f.name) === 'pdf');
  const words = files.filter(f => ['docx','doc'].includes(getExt(f.name)));
  const excels= files.filter(f => ['xlsx','xls'].includes(getExt(f.name)));
  const texts = files.filter(f => ['txt','csv','md'].includes(getExt(f.name)));
  const imgs  = files.filter(f => isImage(f.name));

  try {
    if (zips.length)  for (const z of zips)  await handleZip(z);
    if (pdfs.length)  for (const p of pdfs)  await handlePDF(p);
    if (words.length) for (const w of words) await handleWord(w);
    if (excels.length)for (const x of excels)await handleExcel(x);
    if (texts.length) for (const t of texts) await handleText(t);
    if (imgs.length)  await handleImages(imgs);

    if (state.results.length === 0) {
      showToast('No supported files found.', 'error');
      processingSection.classList.add('hidden'); return;
    }
    if (!state.cancelled) { clearInterval(state.timerInterval); setProgress(100,'Complete!'); setTimeout(showResults, 500); }
  } catch (err) {
    console.error(err);
    showToast('Error: ' + err.message, 'error');
    processingSection.classList.add('hidden');
  }
}

// ─── ZIP Handler ──────────────────────────────────────────────────
async function handleZip(file) {
  processingTitle.textContent = `Reading ZIP: ${file.name}`;
  processingModeBadge.textContent = '📦 ZIP Archive';
  setProgress(0, 'Loading archive…');
  showToast(`Opening ${file.name} (${formatBytes(file.size)})…`, 'info');

  const zip = await JSZip.loadAsync(file, {
    onprogress: m => setProgress(m.percent * 0.2, `Extracting… ${Math.round(m.percent)}%`)
  });
  
  const imageFiles = [];
  const otherFiles = [];
  
  zip.forEach((path, entry) => { 
    if (entry.dir) return;
    if (isImage(path)) imageFiles.push({ path, entry }); 
    else if (['pdf','docx','doc','xlsx','xls','txt','csv'].includes(getExt(path))) otherFiles.push({ path, entry });
  });

  if (!imageFiles.length && !otherFiles.length) { showToast(`No supported files found inside ${file.name}`, 'error'); return; }
  
  // Route non-image files first
  if (otherFiles.length) {
    setProgress(20, `Routing ${otherFiles.length} documents from ZIP...`);
    for (const {path, entry} of otherFiles) {
      const blob = await entry.async('blob');
      // Create a File object so the other handlers can read it
      const extractedFile = new File([blob], path, { type: blob.type || 'application/octet-stream' });
      await handleFiles([extractedFile]);
    }
  }

  // Route images to batch OCR
  if (imageFiles.length) {
    statTotal.textContent = parseInt(statTotal.textContent||0) + imageFiles.length;
    const items = [];
    for (let i = 0; i < imageFiles.length; i++) {
      if (state.cancelled) break;
      const { path, entry } = imageFiles[i];
      const blob = await entry.async('blob');
      items.push({ name: path, blob, sourceFile: file.name });
      setProgress(20 + (i/imageFiles.length)*10, `Preparing images… ${i+1}/${imageFiles.length}`);
    }
    await runOCRBatch(items, 30, 'image');
  }
}

// ─── Image Handler ────────────────────────────────────────────────
async function handleImages(files) {
  processingTitle.textContent = `Processing ${files.length} image(s)…`;
  processingModeBadge.textContent = '🖼️ Direct Images';
  statTotal.textContent = parseInt(statTotal.textContent||0) + files.length;
  const items = files.map(f => ({ name: f.name, blob: f, sourceFile: f.name }));
  await runOCRBatch(items, 5, 'image');
}

// ─── PDF Handler — Pure Image-Based Pipeline ─────────────────────
// Every page is rendered to a high-res canvas, preprocessed as an image,
// then fed to Tesseract. No font/text layer is ever used.
async function handlePDF(file) {
  if (typeof pdfjsLib === 'undefined') {
    showToast('PDF.js not loaded. Check your internet.', 'error'); return;
  }
  const settings = getSettings();
  processingTitle.textContent = `Rendering PDF as images: ${file.name}`;
  processingModeBadge.textContent = '📄 PDF → Image → OCR';
  setProgress(0, 'Loading PDF…');

  const loadTask = pdfjsLib.getDocument({
    data: await file.arrayBuffer(),
    disableFontFace: true,
    nativeImageDecoderSupport: 'none',
    useWorkerFetch: false,
  });
  const pdf = await loadTask.promise;
  const totalPages = pdf.numPages;
  statTotal.textContent = parseInt(statTotal.textContent||0) + totalPages;
  showToast(`PDF: ${totalPages} page(s) — image-first OCR (no text layer)…`, 'info');

  const RENDER_SCALE = Math.max(settings.pdfScale, 2.5);

  const items = [];
  for (let i = 1; i <= totalPages; i++) {
    if (state.cancelled) break;
    setProgress(5 + (i / totalPages) * 20, `Rendering page ${i}/${totalPages} at ${RENDER_SCALE}×…`);

    const page     = await pdf.getPage(i);
    const viewport = page.getViewport({ scale: RENDER_SCALE });
    const shortName = `${file.name} — Page ${i}`;

    const canvas = document.createElement('canvas');
    canvas.width  = viewport.width;
    canvas.height = viewport.height;
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    await page.render({ canvasContext: ctx, viewport }).promise;
    const previewUrl = canvas.toDataURL('image/jpeg', 0.7);

    const processedDataUrl = preprocessPDFCanvas(canvas, settings.preprocess);
    items.push({
      name:        shortName,
      dataUrl:     processedDataUrl,
      previewUrl:  previewUrl,
      sourceFile:  file.name,
      pageNum:     i,
    });
  }

  await runOCRBatch(items, 25, 'pdf');
}

// ─── PDF Canvas Preprocessor ──────────────────────────────────────
// High-Accuracy Mode: Runs a highly optimized 3x3 unsharp mask kernel
// across the canvas to crisp up Devanagari Shirorekha (top-lines) 
// without blocking the main thread for too long.
function preprocessPDFCanvas(srcCanvas, doPreprocess) {
  if (!doPreprocess) return srcCanvas.toDataURL('image/jpeg', 0.95);

  const w = srcCanvas.width, h = srcCanvas.height;
  const dst = document.createElement('canvas');
  dst.width = w; dst.height = h;
  const ctx = dst.getContext('2d', { willReadFrequently: true });
  ctx.drawImage(srcCanvas, 0, 0);

  const id = ctx.getImageData(0, 0, w, h);
  const d = id.data;

  // Gentle contrast boost for rendered PDF pages (keeps Devanagari marks intact)
  for (let i = 0; i < d.length; i += 4) {
    let g = 0.299 * d[i] + 0.587 * d[i + 1] + 0.114 * d[i + 2];
    g = Math.max(0, Math.min(255, (g - 128) * 1.15 + 128));
    d[i] = d[i + 1] = d[i + 2] = g;
  }
  ctx.putImageData(id, 0, 0);

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

  return dst.toDataURL('image/jpeg', 0.95);
}

// ─── Word Handler — Render DOCX to image, then OCR ───────────────
async function renderDocxToDataUrl(file) {
  if (typeof docx === 'undefined' || typeof html2canvas === 'undefined') {
    throw new Error('DOCX render libraries not loaded. Check your internet.');
  }

  const container = document.createElement('div');
  container.style.cssText = [
    'position:fixed',
    'left:-10000px',
    'top:0',
    'width:900px',
    'background:#ffffff',
    'padding:48px',
    'box-sizing:border-box',
    'font-family:"Noto Sans Devanagari",sans-serif',
  ].join(';');
  document.body.appendChild(container);

  try {
    await docx.renderAsync(await file.arrayBuffer(), container, null, {
      className: 'docx-preview-root',
      inWrapper: true,
      ignoreWidth: false,
      ignoreHeight: false,
      breakPages: true,
    });

    const canvas = await html2canvas(container, {
      backgroundColor: '#ffffff',
      scale: 2,
      useCORS: true,
      logging: false,
    });
    return canvas.toDataURL('image/png', 1.0);
  } finally {
    document.body.removeChild(container);
  }
}

async function handleWord(file) {
  processingTitle.textContent = `Rendering Word as image: ${file.name}`;
  processingModeBadge.textContent = '📝 DOCX → Image → OCR';
  setProgress(10, 'Rendering Word document…');
  statTotal.textContent = parseInt(statTotal.textContent || 0) + 1;

  try {
    const dataUrl = await renderDocxToDataUrl(file);
    const settings = getSettings();
    const processedDataUrl = settings.preprocess
      ? await preprocessDataUrlCanvas(dataUrl)
      : dataUrl;

    await runOCRBatch([{
      name: file.name,
      dataUrl: processedDataUrl,
      previewUrl: dataUrl,
      sourceFile: file.name,
    }], 70, 'word');
  } catch (err) {
    statFailed.textContent = parseInt(statFailed.textContent || 0) + 1;
    state.results.push({
      filename: file.name,
      shortName: file.name,
      blob: null,
      imageUrl: null,
      text: '',
      confidence: 0,
      words: 0,
      status: 'failed',
      error: err.message,
      fileType: 'word',
    });
    showToast(`Word OCR failed: ${err.message}`, 'error');
  }
}

function preprocessDataUrlCanvas(dataUrl) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => {
      const canvas = document.createElement('canvas');
      canvas.width = img.width;
      canvas.height = img.height;
      const ctx = canvas.getContext('2d');
      ctx.drawImage(img, 0, 0);
      resolve(preprocessPDFCanvas(canvas, true));
    };
    img.onerror = () => reject(new Error('Failed to preprocess rendered Word image'));
    img.src = dataUrl;
  });
}

// ─── Excel Handler ────────────────────────────────────────────────
async function handleExcel(file) {
  if (typeof XLSX === 'undefined') {
    showToast('SheetJS not loaded. Check your internet.', 'error'); return;
  }
  processingTitle.textContent = `Reading Excel: ${file.name}`;
  processingModeBadge.textContent = '📊 Spreadsheet';
  setProgress(10, 'Extracting Spreadsheet…');

  const qEl = createQueueItem(file.name, 'processing');
  try {
    const arrayBuffer = await file.arrayBuffer();
    const workbook = XLSX.read(arrayBuffer, {type: 'array'});
    let text = '';
    
    workbook.SheetNames.forEach(sheetName => {
      const sheet = workbook.Sheets[sheetName];
      const csv = XLSX.utils.sheet_to_csv(sheet);
      if (csv.trim()) {
        text += `\n--- Sheet: ${sheetName} ---\n\n` + csv + '\n';
      }
    });
    
    text = text.trim();
    const words = text ? text.split(/\s+/).filter(Boolean).length : 0;

    statTotal.textContent = parseInt(statTotal.textContent||0) + 1;
    statProcessed.textContent = parseInt(statProcessed.textContent||0) + 1;
    statWords.textContent = parseInt(statWords.textContent||0) + words;
    qEl.className = 'queue-item done';
    setProgress(90, `Excel extracted: ${words} words`);

    state.results.push({
      filename: file.name, shortName: file.name, blob: null, imageUrl: null,
      text, confidence: 100, words, status: text ? 'success' : 'empty',
      fileType: 'excel', sourceFile: file.name,
    });
  } catch (err) {
    qEl.className = 'queue-item failed';
    statFailed.textContent = parseInt(statFailed.textContent||0) + 1;
    state.results.push({ filename: file.name, shortName: file.name, blob: null, imageUrl: null, text: '', confidence: 0, words: 0, status: 'failed', error: err.message, fileType: 'excel' });
  }
}

// ─── Text Handler ─────────────────────────────────────────────────
async function handleText(file) {
  processingTitle.textContent = `Reading: ${file.name}`;
  processingModeBadge.textContent = '📃 Plain Text';
  setProgress(10, 'Reading file…');
  const qEl = createQueueItem(file.name, 'processing');
  try {
    const text = await file.text();
    const words = text.trim().split(/\s+/).filter(Boolean).length;
    statTotal.textContent = parseInt(statTotal.textContent||0) + 1;
    statProcessed.textContent = parseInt(statProcessed.textContent||0) + 1;
    statWords.textContent = parseInt(statWords.textContent||0) + words;
    qEl.className = 'queue-item done';
    setProgress(90, `Text read: ${words} words`);
    state.results.push({ filename: file.name, shortName: file.name, blob: null, imageUrl: null, text, confidence: 100, words, status: text.trim() ? 'success' : 'empty', fileType: 'text', sourceFile: file.name });
  } catch (err) {
    qEl.className = 'queue-item failed';
    statFailed.textContent = parseInt(statFailed.textContent||0) + 1;
    state.results.push({ filename: file.name, shortName: file.name, blob: null, imageUrl: null, text: '', confidence: 0, words: 0, status: 'failed', error: err.message, fileType: 'text' });
  }
}

// ─── OCR Batch Runner (for images, ZIP contents, PDF pages) ──────
async function runOCRBatch(items, startPct, fileType) {
  if (!items.length) return;
  const settings = getSettings();
  const total = items.length;
  let processed = 0;
  let totalWordsSoFar = parseInt(statWords.textContent || 0);

  // Init workers (Maximize parallel processing for speed)
  const maxHardwareWorkers = navigator.hardwareConcurrency ? Math.min(navigator.hardwareConcurrency, 12) : 4;
  const numWorkers = settings.concurrent ? Math.min(maxHardwareWorkers, total) : 1;
  const workers = [];
  for (let w = 0; w < numWorkers; w++) {
    try {
      const langCombo = settings.lang === 'eng' ? 'eng' : `${settings.lang}+eng`;
      const worker = await Tesseract.createWorker(langCombo, 1, {
        langPath: 'https://tessdata.projectnaptha.com/4.0.0_best',
        logger: () => {} 
      });
      await worker.setParameters({
        tessedit_pageseg_mode: settings.psm,
        preserve_interword_spaces: '1',
        textord_tabfind_find_tables: '1',
      });
      workers.push(worker);
    } catch (e) { console.warn('Worker init failed', e); }
  }
  if (!workers.length) { showToast('OCR engine failed to initialize.', 'error'); return; }
  state.activeWorkers.push(...workers);

  const queue = [...items];

  async function runWorker(worker) {
    while (queue.length > 0 && !state.cancelled) {
      const item = queue.shift();
      if (!item) break;
      const shortName = item.name.split('/').pop();
      const qEl = createQueueItem(shortName, 'processing');
      try {
        let ocrInput, displayUrl;

        if (item.dataUrl) {
          // ── PDF path: already preprocessed as image, use directly ──
          // No additional processing — the image pipeline already ran
          ocrInput   = item.dataUrl;
          displayUrl = item.previewUrl || item.dataUrl; // show colour preview if available
        } else {
          // ── Image/ZIP path: standard preprocessing ─────────────────
          ocrInput   = await prepareImage(item.blob, settings.quality, settings.preprocess);
          displayUrl = ocrInput;
        }

        const result = await worker.recognize(ocrInput);

        // ─── Table Reconstruction & Noise Filter ───────────────────
        // Tesseract struggles with table grid lines and often outputs them
        // as garbage characters (|, _, [, ]). By using bounding boxes, we can 
        // reconstruct the table purely from valid words and ignore grid lines.
        const cleanWords = (result.data.words || []).filter(w => {
           const t = w.text.trim();
           // Ignore noise/borders: pure underscores, pipes, brackets, hyphens
           if (/^[_|\[\]\-\\/=]+$/.test(t)) return false; 
           
           // Ignore Emblem/Logo hallucinations (low confidence english/symbols in Nepali context)
           if (w.confidence < 60 && /^[a-zA-Z&@#]+$/.test(t) && t.length < 4) return false;
           if (w.confidence < 45 && t.length < 3) return false;
           
           return t.length > 0;
        });

        // Group words into rows based on vertical centre overlap
        cleanWords.sort((a, b) => a.bbox.y0 - b.bbox.y0);
        const rows = [];
        let currentRow = [];
        for (const w of cleanWords) {
           if (currentRow.length === 0) {
              currentRow.push(w);
           } else {
              const prev = currentRow[currentRow.length - 1];
              const height = Math.max(1, prev.bbox.y1 - prev.bbox.y0);
              const cy1 = (w.bbox.y0 + w.bbox.y1) / 2;
              const cy2 = (prev.bbox.y0 + prev.bbox.y1) / 2;
              // If vertical centres align within half a character height, it's the same row
              if (Math.abs(cy1 - cy2) < height * 0.6) {
                 currentRow.push(w);
              } else {
                 rows.push(currentRow);
                 currentRow = [w];
              }
           }
        }
        if (currentRow.length > 0) rows.push(currentRow);

        // Sort horizontally and separate true columns with Tabs (great for Excel),
        // but use normal spaces for standard sentences to avoid huge gaps.
        const tableText = rows.map(row => {
           row.sort((a, b) => a.bbox.x0 - b.bbox.x0);
           let lineStr = '';
           for (let i = 0; i < row.length; i++) {
              if (i === 0) {
                 lineStr += row[i].text;
              } else {
                 const prev = row[i - 1];
                 const curr = row[i];
                 // Calculate physical gap between previous word and current word
                 const gap = curr.bbox.x0 - prev.bbox.x1;
                 const prevHeight = Math.max(1, prev.bbox.y1 - prev.bbox.y0);
                 
                 // If the gap is > 1.5x the character height, it's a structural column jump
                 if (gap > prevHeight * 1.5) {
                    lineStr += '\t' + curr.text;
                 } else {
                    lineStr += ' ' + curr.text;
                 }
              }
           }
           return lineStr;
        }).join('\n');

        const text = tableText || result.data.text.trim();
        // ─────────────────────────────────────────────────────────

        const confidence = Math.round(result.data.confidence);
        const words  = text ? text.split(/\s+/).filter(Boolean).length : 0;

        qEl.className = 'queue-item done';
        totalWordsSoFar += words;
        statWords.textContent = totalWordsSoFar;

        state.results.push({
          filename:    item.name,
          shortName,
          blob:        item.blob || null,
          imageUrl:    displayUrl,
          text, confidence, words,
          status:      text ? 'success' : 'empty',
          fileType:    fileType || 'image',
          pageNum:     item.pageNum,
          sourceFile:  item.sourceFile,
        });

        // Only revoke object URLs (not dataURLs)
        if (displayUrl && displayUrl.startsWith('blob:')) URL.revokeObjectURL(displayUrl);

      } catch (err) {
        qEl.className = 'queue-item failed';
        statFailed.textContent = parseInt(statFailed.textContent||0) + 1;
        state.results.push({
          filename: item.name, shortName,
          blob: item.blob||null, imageUrl: item.previewUrl||null,
          text: '', confidence: 0, words: 0,
          status: 'failed', error: err.message,
          fileType: fileType || 'image',
        });
      }
      processed++;
      statProcessed.textContent = parseInt(statProcessed.textContent||0) + 1;
      const pct = startPct + Math.round((processed / total) * (95 - startPct));
      setProgress(pct, `OCR: ${processed}/${total} items`);
    }
  }

  await Promise.all(workers.map(w => runWorker(w)));
  await Promise.all(workers.map(w => w.terminate().catch(() => {})));
  state.activeWorkers = state.activeWorkers.filter(w => !workers.includes(w));
}

// ─── Image Pre-processing ─────────────────────────────────────────
async function prepareImage(blob, quality, preprocess) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    const objUrl = URL.createObjectURL(blob);
    img.onload = () => {
      URL.revokeObjectURL(objUrl);
      const canvas = document.createElement('canvas');
      let { width, height } = img;
      if (quality === 'high') {
        const s = Math.min(3000 / Math.max(width, height), 2.5);
        if (s > 1) { width = Math.round(width*s); height = Math.round(height*s); }
      } else if (quality === 'medium') {
        const s = Math.min(2000 / Math.max(width, height), 1.5);
        if (s > 1) { width = Math.round(width*s); height = Math.round(height*s); }
      }
      canvas.width = width; canvas.height = height;
      const ctx = canvas.getContext('2d');
      ctx.imageSmoothingEnabled = true; ctx.imageSmoothingQuality = 'high';
      ctx.drawImage(img, 0, 0, width, height);
      if (preprocess) applyPreprocessing(ctx, width, height);
      resolve(canvas.toDataURL('image/png', 1.0));
    };
    img.onerror = () => { URL.revokeObjectURL(objUrl); reject(new Error('Image load failed')); };
    img.src = objUrl;
  });
}

function applyPreprocessing(ctx, w, h) {
  const id = ctx.getImageData(0, 0, w, h); const d = id.data;
  for (let i = 0; i < d.length; i += 4) { const g = 0.299*d[i]+0.587*d[i+1]+0.114*d[i+2]; d[i]=d[i+1]=d[i+2]=g; }
  ctx.putImageData(id, 0, 0);
  const sh = ctx.getImageData(0, 0, w, h); const src = new Uint8ClampedArray(sh.data);
  const k = [0,-1,0,-1,5,-1,0,-1,0];
  for (let y=1;y<h-1;y++) for (let x=1;x<w-1;x++) {
    let r=0; for (let ky=-1;ky<=1;ky++) for (let kx=-1;kx<=1;kx++) { r+=src[((y+ky)*w+(x+kx))*4]*k[(ky+1)*3+(kx+1)]; }
    const idx=(y*w+x)*4; sh.data[idx]=sh.data[idx+1]=sh.data[idx+2]=Math.max(0,Math.min(255,r));
  }
  ctx.putImageData(sh, 0, 0);
}

// ─── State Helpers ────────────────────────────────────────────────
function resetState() {
  state.results=[]; state.cancelled=false; state.startTime=Date.now();
  clearInterval(state.timerInterval);
  state.timerInterval = setInterval(() => {
    const e = Math.floor((Date.now()-state.startTime)/1000);
    statTime.textContent = e>=60?`${Math.floor(e/60)}m${e%60}s`:`${e}s`;
  }, 1000);
  liveQueue.innerHTML=''; statProcessed.textContent='0'; statFailed.textContent='0';
  statWords.textContent='0'; statTime.textContent='0s'; statTotal.textContent='0';
}

function setProgress(pct, label) {
  progressFill.style.width = `${Math.min(100,pct)}%`;
  progressPct.textContent = `${Math.round(pct)}%`;
  if (label) progressLabel.textContent = label;
}

function createQueueItem(name, status) {
  const el = document.createElement('div');
  el.className = `queue-item ${status}`;
  el.innerHTML = `<span class="queue-dot"></span><span class="queue-filename" title="${name}">${name}</span>`;
  liveQueue.appendChild(el);
  liveQueue.scrollTop = liveQueue.scrollHeight;
  return el;
}

// ─── Cancel ───────────────────────────────────────────────────────
cancelBtn.addEventListener('click', async () => {
  state.cancelled = true; cancelBtn.disabled = true; cancelBtn.textContent = 'Cancelling…';
  for (const w of state.activeWorkers) try { await w.terminate(); } catch {}
  state.activeWorkers = [];
  clearInterval(state.timerInterval);
  processingSection.classList.add('hidden');
  showToast('Processing cancelled.', 'info'); fileInput.value='';
});

// ─── Show Results ─────────────────────────────────────────────────
function showResults() {
  processingSection.classList.add('hidden');
  resultsSection.classList.remove('hidden');
  const total = state.results.length;
  const success = state.results.filter(r=>r.status==='success').length;
  const failed = state.results.filter(r=>r.status==='failed').length;
  const empty = state.results.filter(r=>r.status==='empty').length;
  const totalWords = state.results.reduce((a,r)=>a+r.words,0);
  const elapsed = Math.round((Date.now()-state.startTime)/1000);
  resultsMeta.textContent = `${total} items — ${success} with text, ${empty} empty, ${failed} failed — ${totalWords.toLocaleString()} words — ${elapsed}s`;
  renderResults();
}

// ─── Render Results ───────────────────────────────────────────────
function renderResults() {
  const filter = state.filterMode, query = state.searchQuery.toLowerCase();
  const filtered = state.results.filter(r => {
    if (filter==='success' && r.status!=='success') return false;
    if (filter==='failed' && r.status!=='failed') return false;
    if (filter==='empty' && r.status!=='empty') return false;
    if (query && !r.text.toLowerCase().includes(query) && !r.shortName.toLowerCase().includes(query)) return false;
    return true;
  });
  resultsGrid.innerHTML = '';
  if (!filtered.length) {
    resultsGrid.innerHTML = `<div class="no-results"><p style="font-size:2rem;margin-bottom:.5rem">🔍</p><p>No results match your filter.</p></div>`;
    return;
  }
  filtered.forEach((r, i) => { resultsGrid.appendChild(createResultCard(r, i)); });
}

function getFileTypeInfo(fileType) {
  return FILE_TYPES[fileType] || FILE_TYPES.image;
}

function createResultCard(r, index) {
  const card = document.createElement('div');
  card.className = `result-card status-${r.status}`;
  card.style.animationDelay = `${Math.min(index*0.04,0.5)}s`;

  const confidence = r.confidence || 0;
  const confColor = confidence>=80?'#10B981':confidence>=50?'#F59E0B':'#EF4444';
  const badgeClass = r.status==='success'?'badge-success':r.status==='failed'?'badge-failed':'badge-empty';
  const badgeLabel = r.status==='success'?'Text Found':r.status==='failed'?'Failed':'No Text';
  const hasText = r.text && r.text.length>0;
  const typeInfo = getFileTypeInfo(r.fileType);
  const showConf = $('toggleConfidence').checked;

  // Image panel or doc icon
  let mediaHtml;
  if (r.imageUrl || (r.blob && r.fileType==='image')) {
    const src = r.imageUrl || (r.blob ? URL.createObjectURL(r.blob) : '');
    mediaHtml = `
      <div class="card-image-wrapper">
        <img class="card-image" src="${src}" alt="${escapeHtml(r.shortName)}" loading="lazy"/>
        <span class="card-status-badge ${badgeClass}">${badgeLabel}</span>
        ${showConf?`<div class="confidence-bar"><div class="confidence-fill" style="width:${confidence}%;background:${confColor}"></div></div>`:''}
      </div>`;
  } else {
    mediaHtml = `
      <div class="card-image-wrapper">
        <div class="card-doc-icon ${typeInfo.badgeClass}">
          <span>${typeInfo.icon}</span>
          <span class="card-doc-label">${typeInfo.label}</span>
        </div>
        <span class="card-status-badge ${badgeClass}">${badgeLabel}</span>
      </div>`;
  }

  const previewText = hasText ? escapeHtml(r.text.substring(0,300)) : (r.status==='failed'?`Error: ${escapeHtml(r.error||'Unknown')}`:'No text detected.');

  card.innerHTML = `
    ${mediaHtml}
    <div class="card-body">
      <div class="card-filename" title="${escapeHtml(r.filename)}">${escapeHtml(r.shortName)}</div>
      <div class="card-meta">
        <span style="color:${typeInfo.color};font-weight:700">${typeInfo.icon} ${typeInfo.label}</span>
        ${r.pageNum?`<span>· Page ${r.pageNum}</span>`:''}
        ${showConf&&r.fileType!=='word'&&r.fileType!=='text'?`<span style="color:${confColor}">· ${confidence}% conf</span>`:''}
        <span>· ${r.words.toLocaleString()} words</span>
      </div>
      <div class="card-text-preview ${hasText?'has-text':'no-text'}">${previewText}</div>
      <div class="card-actions">
        <button class="card-btn" onclick="copyText(${state.results.indexOf(r)})">Copy Text</button>
        <button class="card-btn" onclick="openPreview(${state.results.indexOf(r)})">View Full</button>
      </div>
    </div>`;

  card.addEventListener('click', e => { if (!e.target.closest('.card-btn')) openPreview(state.results.indexOf(r)); });
  return card;
}

// ─── Copy ─────────────────────────────────────────────────────────
window.copyText = async idx => {
  const r = state.results[idx];
  if (!r) return;
  try { await navigator.clipboard.writeText(r.text); showToast('Text copied!','success'); }
  catch { showToast('Copy failed — try selecting manually.','error'); }
};

// ─── Preview Modal ────────────────────────────────────────────────
window.openPreview = idx => {
  const r = state.results[idx];
  if (!r) return;
  const typeInfo = getFileTypeInfo(r.fileType);
  modalTitle.textContent = r.shortName;
  modalTypeBadge.textContent = typeInfo.label;
  modalTypeBadge.className = `modal-type-badge ${typeInfo.badgeClass}`;
  modalTextarea.value = r.text || '(No text extracted)';

  const showConf = $('toggleConfidence').checked;
  const c = r.confidence||0;
  const cc = c>=80?'#10B981':c>=50?'#F59E0B':'#EF4444';
  modalConfidence.innerHTML = (showConf && r.fileType!=='word'&&r.fileType!=='text')
    ? `<span style="color:${cc};font-weight:600">⬤ Confidence: ${c}%</span> &nbsp;·&nbsp; ${r.words} words &nbsp;·&nbsp; ${r.text.length} chars`
    : `${r.words.toLocaleString()} words &nbsp;·&nbsp; ${r.text.length.toLocaleString()} chars`;

  // Show image or doc icon
  if (r.imageUrl) {
    modalImagePanel.innerHTML = `<img id="modalImage" src="${r.imageUrl}" alt="${escapeHtml(r.shortName)}" style="max-width:100%;max-height:500px;object-fit:contain;"/>`;
    modalImagePanel.classList.remove('hidden');
    modalBody.classList.remove('text-only');
  } else {
    modalImagePanel.innerHTML = `<div class="doc-icon-large">${typeInfo.icon}<br/><span style="font-size:1rem;color:#94A3B8">${r.filename}</span></div>`;
    modalImagePanel.classList.remove('hidden');
    modalBody.classList.remove('text-only');
  }

  previewModal.classList.remove('hidden');
  document.body.style.overflow = 'hidden';
};

modalClose.addEventListener('click', closeModal);
previewModal.addEventListener('click', e => { if (e.target===previewModal) closeModal(); });
document.addEventListener('keydown', e => { if (e.key==='Escape') closeModal(); });
function closeModal() { previewModal.classList.add('hidden'); document.body.style.overflow=''; }

$('copyModalText').addEventListener('click', async () => {
  try { await navigator.clipboard.writeText($('modalTextarea').value); showToast('Text copied!','success'); }
  catch { showToast('Copy failed.','error'); }
});

// ─── Search & Filter ──────────────────────────────────────────────
searchInput.addEventListener('input', () => { state.searchQuery=searchInput.value; renderResults(); });
document.querySelectorAll('.filter-tab').forEach(tab => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.filter-tab').forEach(t=>t.classList.remove('active'));
    tab.classList.add('active'); state.filterMode=tab.dataset.filter; renderResults();
  });
});
$('viewGrid').addEventListener('click', () => { state.currentView='grid'; $('viewGrid').classList.add('active'); $('viewList').classList.remove('active'); resultsGrid.classList.remove('list-view'); });
$('viewList').addEventListener('click', () => { state.currentView='list'; $('viewList').classList.add('active'); $('viewGrid').classList.remove('active'); resultsGrid.classList.add('list-view'); });

// ─── Exports ──────────────────────────────────────────────────────
$('exportTxtBtn').addEventListener('click', () => {
  const lines = state.results.map(r=>`===== ${r.filename} [${(r.fileType||'').toUpperCase()}] =====\nWords: ${r.words} | Confidence: ${r.confidence}%\n\n${r.text||'(No text)'}\n`);
  downloadFile(lines.join('\n'+'─'.repeat(60)+'\n\n'),'zipscan_results.txt','text/plain');
  showToast('TXT exported!','success');
});
$('exportJsonBtn').addEventListener('click', () => {
  const data = state.results.map(r=>({ filename:r.filename, type:r.fileType, status:r.status, confidence:r.confidence, words:r.words, chars:r.text.length, text:r.text }));
  downloadFile(JSON.stringify({exportedAt:new Date().toISOString(),total:data.length,results:data},null,2),'zipscan_results.json','application/json');
  showToast('JSON exported!','success');
});
$('exportCsvBtn').addEventListener('click', () => {
  const header='filename,type,status,confidence,words,characters,text';
  const rows=state.results.map(r=>[r.filename,r.fileType||'',r.status,r.confidence,r.words,r.text.length,`"${r.text.replace(/"/g,'""')}"`].join(','));
  downloadFile([header,...rows].join('\n'),'zipscan_results.csv','text/csv');
  showToast('CSV exported!','success');
});
$('clearBtn').addEventListener('click', () => { state.results=[]; resultsSection.classList.add('hidden'); fileInput.value=''; clearInterval(state.timerInterval); showToast('Ready for new upload!','info'); });

function downloadFile(content, filename, mimeType) {
  const blob = new Blob([content],{type:mimeType});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a'); a.href=url; a.download=filename; a.click();
  URL.revokeObjectURL(url);
}

// ─── Toast ────────────────────────────────────────────────────────
function showToast(msg, type='info') {
  const icons = {
    success:`<svg width="18" height="18" viewBox="0 0 18 18" fill="none"><circle cx="9" cy="9" r="8" fill="#059669"/><path d="M5.5 9l2.5 2.5 4.5-5" stroke="white" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>`,
    error:`<svg width="18" height="18" viewBox="0 0 18 18" fill="none"><circle cx="9" cy="9" r="8" fill="#DC2626"/><path d="M6 6l6 6M12 6L6 12" stroke="white" stroke-width="1.5" stroke-linecap="round"/></svg>`,
    info:`<svg width="18" height="18" viewBox="0 0 18 18" fill="none"><circle cx="9" cy="9" r="8" fill="#4F46E5"/><path d="M9 8v4M9 6h.01" stroke="white" stroke-width="1.5" stroke-linecap="round"/></svg>`,
  };
  const toast = document.createElement('div');
  toast.className=`toast ${type}`;
  toast.innerHTML=`<span class="toast-icon">${icons[type]}</span><span class="toast-msg">${escapeHtml(msg)}</span>`;
  toastContainer.appendChild(toast);
  setTimeout(()=>{ toast.style.animation='toastOut 0.3s ease forwards'; setTimeout(()=>toast.remove(),300); },4000);
}

// ─── Utils ────────────────────────────────────────────────────────
function formatBytes(b) { if(b<1024)return b+' B'; if(b<1048576)return(b/1024).toFixed(1)+' KB'; return(b/1048576).toFixed(1)+' MB'; }
function escapeHtml(s) { return (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }

// ─── Init ─────────────────────────────────────────────────────────
showToast('ZipScan Pro ready! 🇳🇵 Image-first Nepali OCR — no Preeti/Kantipur text-layer bias', 'info');
