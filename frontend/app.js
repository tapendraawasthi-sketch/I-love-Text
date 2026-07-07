document.addEventListener('DOMContentLoaded', () => {
    const uploadZone = document.getElementById('upload-zone');
    const fileInput = document.getElementById('file-input');
    const fileInfo = document.getElementById('file-info');
    const fileName = document.getElementById('file-name');
    const fileSize = document.getElementById('file-size');

    const extractBtn = document.getElementById('extract-btn');
    const langSelect = document.getElementById('lang-select');
    const modeSelect = document.getElementById('mode-select');
    const btnText = document.querySelector('.btn-text');
    const spinner = document.querySelector('.spinner');

    const errorBanner = document.getElementById('error-banner');
    const errorMessage = document.getElementById('error-message');

    const resultSection = document.getElementById('result-section');
    const resultText = document.getElementById('result-text');
    const copyBtn = document.getElementById('copy-btn');
    const downloadBtn = document.getElementById('download-btn');
    const metaPanel = document.getElementById('meta-panel');

    const progressSection = document.getElementById('progress-section');
    const progressFill = document.getElementById('progress-fill');
    const progressLabel = document.getElementById('progress-label');

    let currentFile = null;
    const MAX_MB = 100;

    function showDeviceProfile() {
        const badge = document.getElementById('device-badge');
        if (!badge) return;
        
        const mode = modeSelect?.value || 'direct';
        if (mode === 'direct' || mode === 'auto') {
            badge.textContent = 'Direct extraction mode — reads PDF text layer with font conversion (no image processing)';
            badge.className = 'device-badge tier-high';
        } else if (window.TextExtractOCR?.getDeviceProfile) {
            const p = window.TextExtractOCR.getDeviceProfile();
            badge.textContent = `OCR mode · ${p.label} · ${p.cores} cores · ${p.ramGb} GB RAM`;
            badge.className = `device-badge tier-${p.tier}`;
        }
    }

    showDeviceProfile();
    modeSelect?.addEventListener('change', showDeviceProfile);

    uploadZone.addEventListener('click', () => fileInput.click());

    uploadZone.addEventListener('dragover', (e) => {
        e.preventDefault();
        uploadZone.classList.add('dragover');
    });

    uploadZone.addEventListener('dragleave', () => {
        uploadZone.classList.remove('dragover');
    });

    uploadZone.addEventListener('drop', (e) => {
        e.preventDefault();
        uploadZone.classList.remove('dragover');
        if (e.dataTransfer.files.length) {
            handleFile(e.dataTransfer.files[0]);
        }
    });

    fileInput.addEventListener('change', () => {
        if (fileInput.files.length) {
            handleFile(fileInput.files[0]);
        }
    });

    function getExt(file) {
        return '.' + file.name.split('.').pop().toLowerCase();
    }

    function handleFile(file) {
        hideError();

        const sizeMb = file.size / (1024 * 1024);
        if (sizeMb > MAX_MB) {
            showError(`File is too large (${sizeMb.toFixed(1)}MB). Max allowed is ${MAX_MB}MB.`);
            currentFile = null;
            updateUI();
            return;
        }

        const allowedExts = ['.pdf', '.docx', '.png', '.jpg', '.jpeg', '.tiff', '.bmp', '.webp'];
        const ext = getExt(file);
        if (!allowedExts.includes(ext)) {
            showError(`File type ${ext} not supported.`);
            currentFile = null;
            updateUI();
            return;
        }

        currentFile = file;
        fileName.textContent = file.name;
        fileSize.textContent = `${sizeMb.toFixed(2)} MB`;
        document.querySelector('.upload-content').classList.add('hidden');
        fileInfo.classList.remove('hidden');
        updateUI();
    }

    function updateUI() {
        extractBtn.disabled = !currentFile;
        if (!currentFile) {
            document.querySelector('.upload-content').classList.remove('hidden');
            fileInfo.classList.add('hidden');
        }
    }

    function setProgress(done, total, label) {
        const pct = total ? Math.round((done / total) * 100) : 0;
        progressFill.style.width = `${pct}%`;
        progressLabel.textContent = label || `${pct}%`;
    }

    function showProgress() {
        progressSection.classList.remove('hidden');
        setProgress(0, 100, 'Starting…');
    }

    function hideProgress() {
        progressSection.classList.add('hidden');
    }

    async function extractViaServer(file, lang, mode, onProgress) {
        onProgress(10, 100, 'Uploading to server…');
        
        const formData = new FormData();
        formData.append('file', file);
        formData.append('lang', lang);
        formData.append('mode', mode);

        const response = await fetch('/api/extract', {
            method: 'POST',
            body: formData,
        });

        onProgress(80, 100, 'Processing…');

        if (!response.ok) {
            const err = await response.json().catch(() => ({}));
            throw new Error(err.detail || `Server error: ${response.status}`);
        }

        const data = await response.json();
        onProgress(100, 100, 'Complete');

        if (!data.success) {
            throw new Error(data.detail || 'Extraction failed');
        }

        return {
            text: data.text,
            meta: {
                ...data.meta,
                mode: data.mode,
                processed_on_server: true,
            },
        };
    }

    async function extractViaBrowser(file, lang, onProgress) {
        if (!window.TextExtractOCR?.isAvailable()) {
            throw new Error('OCR engine not loaded. Check your internet connection and refresh.');
        }

        const ext = getExt(file);
        let result;
        
        if (ext === '.pdf') {
            result = await window.TextExtractOCR.extractPdf(file, lang, onProgress);
        } else if (ext === '.docx') {
            result = await window.TextExtractOCR.extractDocx(file, lang, onProgress);
        } else {
            result = await window.TextExtractOCR.extractImage(file, lang, onProgress);
        }

        return {
            text: result.text,
            meta: {
                ...result.meta,
                processed_locally: true,
            },
        };
    }

    extractBtn.addEventListener('click', async () => {
        if (!currentFile) return;

        hideError();
        resultSection.classList.add('hidden');
        extractBtn.disabled = true;
        spinner.classList.remove('hidden');
        showProgress();

        const lang = langSelect.value;
        const mode = modeSelect?.value || 'direct';
        const ext = getExt(currentFile);
        const onProgress = (done, total, label) => setProgress(done, total, label);

        // Update button text based on mode
        if (mode === 'direct' || mode === 'auto') {
            btnText.textContent = 'Processing on server…';
        } else {
            btnText.textContent = 'Processing in browser (OCR)…';
        }

        try {
            let result;

            // Images always use OCR (no text layer)
            const isImage = ['.png', '.jpg', '.jpeg', '.tiff', '.bmp', '.webp'].includes(ext);

            if (isImage) {
                // Images must use browser OCR
                result = await extractViaBrowser(currentFile, lang, onProgress);
            } else if (mode === 'direct' || mode === 'auto') {
                // PDFs and DOCX: use server for direct extraction
                result = await extractViaServer(currentFile, lang, mode, onProgress);
            } else {
                // OCR mode: use browser
                result = await extractViaBrowser(currentFile, lang, onProgress);
            }

            showResult(result);
        } catch (err) {
            showError(err.message || 'Extraction failed.');
        } finally {
            extractBtn.disabled = false;
            btnText.textContent = 'Extract Text';
            spinner.classList.add('hidden');
            hideProgress();
        }
    });

    function showResult(data) {
        resultText.value = data.text || '';
        resultSection.classList.remove('hidden');

        metaPanel.innerHTML = '';
        if (data.meta) {
            const m = data.meta;
            
            // Mode info
            if (m.mode) {
                const modeLabels = {
                    direct: 'Direct text extraction (95-100% accuracy)',
                    auto: 'Auto (direct + OCR fallback)',
                    ocr: 'Image OCR',
                };
                addMetaItem('Mode', modeLabels[m.mode] || m.mode);
            }
            
            if (m.pages || m.total_pages) {
                addMetaItem('Pages', m.pages || m.total_pages);
            }
            
            // Processing location
            if (m.processed_on_server) {
                addMetaItem('Processing', 'Server (npttf2utf font conversion)');
            } else if (m.processed_locally) {
                addMetaItem('Processing', 'In your browser');
            }
            
            // Direct extraction stats
            if (m.direct_pages != null) {
                addMetaItem('Direct text pages', m.direct_pages);
            }
            if (m.direct_unicode_pages != null) {
                addMetaItem('Unicode pages', m.direct_unicode_pages);
            }
            if (m.direct_legacy_pages != null) {
                addMetaItem('Legacy font pages (converted)', m.direct_legacy_pages);
            }
            
            // Legacy fonts found
            if (m.legacy_fonts_found?.length) {
                addMetaItem('Fonts converted', m.legacy_fonts_found.join(', '));
            }
            
            // OCR stats (if any)
            if (m.ocr_pages != null && m.ocr_pages > 0) {
                addMetaItem('OCR pages', m.ocr_pages);
            }
            if (m.no_text_pages != null && m.no_text_pages > 0) {
                addMetaItem('Scanned pages (OCR)', m.no_text_pages);
            }
            
            // Browser OCR details
            if (m.device_profile) addMetaItem('Device mode', m.device_profile);
            if (m.workers) addMetaItem('OCR workers', m.workers);
            if (m.render_scale) addMetaItem('Render scale', m.render_scale + '×');
            
            // Method and confidence
            if (m.method && !m.mode) {
                addMetaItem('Method', m.method.replace(/_/g, ' '));
            }
            if (m.mean_confidence && m.ocr_pages > 0) {
                addMetaItem('OCR Confidence', `${m.mean_confidence}%`);
            }
        }

        resultSection.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }

    function addMetaItem(label, value) {
        const div = document.createElement('div');
        div.className = 'meta-item';
        div.innerHTML = `<strong>${label}:</strong> ${value}`;
        metaPanel.appendChild(div);
    }

    function showError(msg) {
        errorMessage.textContent = msg;
        errorBanner.classList.remove('hidden');
        errorBanner.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }

    function hideError() {
        errorBanner.classList.add('hidden');
    }

    copyBtn.addEventListener('click', () => {
        if (!resultText.value) return;
        navigator.clipboard.writeText(resultText.value)
            .then(() => {
                const original = copyBtn.textContent;
                copyBtn.textContent = 'Copied!';
                setTimeout(() => { copyBtn.textContent = original; }, 2000);
            })
            .catch(() => alert('Failed to copy text.'));
    });

    downloadBtn.addEventListener('click', () => {
        if (!resultText.value) return;
        const blob = new Blob([resultText.value], { type: 'text/plain;charset=utf-8' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        const baseName = currentFile ? currentFile.name.replace(/\.[^/.]+$/, '') : 'extracted';
        a.download = `${baseName}_extracted.txt`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    });
});
