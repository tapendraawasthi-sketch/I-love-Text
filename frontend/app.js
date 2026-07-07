document.addEventListener('DOMContentLoaded', () => {
    const uploadZone = document.getElementById('upload-zone');
    const fileInput = document.getElementById('file-input');
    const fileInfo = document.getElementById('file-info');
    const fileName = document.getElementById('file-name');
    const fileSize = document.getElementById('file-size');

    const extractBtn = document.getElementById('extract-btn');
    const langSelect = document.getElementById('lang-select');
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
        
        if (window.TextExtractOCR?.getDeviceProfile) {
            const p = window.TextExtractOCR.getDeviceProfile();
            badge.textContent = `${p.label} · ${p.cores} CPU cores · ${p.ramGb} GB RAM · High-res OCR enabled`;
            badge.className = `device-badge tier-${p.tier}`;
        } else {
            badge.textContent = 'Maximum quality OCR mode';
            badge.className = 'device-badge tier-high';
        }
    }

    showDeviceProfile();

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
        const ext = getExt(currentFile);
        const onProgress = (done, total, label) => setProgress(done, total, label);

        btnText.textContent = 'Processing (max quality OCR)…';

        try {
            // All files use browser OCR for maximum quality
            const result = await extractViaBrowser(currentFile, lang, onProgress);
            showResult(result);
        } catch (err) {
            showError(err.message || 'Extraction failed.');
        } finally {
            extractBtn.disabled = false;
            btnText.textContent = 'Extract Text (Max Quality)';
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
            
            if (m.pages) {
                addMetaItem('Pages processed', m.pages);
            }
            
            addMetaItem('Processing', 'In your browser (max quality OCR)');
            
            if (m.device_profile) {
                addMetaItem('Device', m.device_profile);
            }
            
            if (m.render_scale) {
                addMetaItem('Render scale', m.render_scale + '× (high resolution)');
            }
            
            if (m.mean_confidence) {
                addMetaItem('OCR Confidence', `${m.mean_confidence}%`);
            }
            
            if (m.workers) {
                addMetaItem('OCR workers used', m.workers);
            }
            
            if (m.quality_retry) {
                addMetaItem('Quality boost', 'Weak pages re-processed at higher resolution');
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
