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

    let currentFile = null;
    const MAX_MB = 25;

    // --- File Selection ---
    
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

    function handleFile(file) {
        hideError();
        
        // Client validation
        const sizeMb = file.size / (1024 * 1024);
        if (sizeMb > MAX_MB) {
            showError(`File is too large (${sizeMb.toFixed(1)}MB). Max allowed is ${MAX_MB}MB.`);
            currentFile = null;
            updateUI();
            return;
        }

        const allowedExts = ['.pdf', '.docx', '.png', '.jpg', '.jpeg', '.tiff', '.bmp', '.webp'];
        const ext = '.' + file.name.split('.').pop().toLowerCase();
        if (!allowedExts.includes(ext)) {
            showError(`File type ${ext} not supported.`);
            currentFile = null;
            updateUI();
            return;
        }

        currentFile = file;
        
        // Show file info
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

    // --- API Request ---
    
    extractBtn.addEventListener('click', async () => {
        if (!currentFile) return;

        // UI Loading state
        hideError();
        resultSection.classList.add('hidden');
        extractBtn.disabled = true;
        btnText.textContent = 'Extracting… (large PDFs may take several minutes)';
        btnText.classList.remove('hidden');
        spinner.classList.remove('hidden');

        const formData = new FormData();
        formData.append('file', currentFile);
        formData.append('lang', langSelect.value);

        try {
            const controller = new AbortController();
            const timeoutMs = 15 * 60 * 1000;
            const timeoutId = setTimeout(() => controller.abort(), timeoutMs);

            const response = await fetch('/api/extract', {
                method: 'POST',
                body: formData,
                signal: controller.signal,
            });
            clearTimeout(timeoutId);

            const data = await response.json().catch(() => ({}));

            if (!response.ok) {
                if (response.status === 502 || response.status === 503) {
                    throw new Error(
                        'Server overloaded or timed out (502). Large PDFs take several minutes on the free tier — try splitting into smaller files, or retry in a moment.'
                    );
                }
                if (response.status === 507) {
                    throw new Error(data.detail || 'Server ran out of memory processing this file. Try a smaller PDF.');
                }
                throw new Error(data.detail || data.error || `Server error (${response.status})`);
            }

            if (!data.success) {
                throw new Error(data.detail || 'Extraction failed.');
            }

            // Success
            showResult(data);

        } catch (err) {
            if (err.name === 'AbortError') {
                showError('Request timed out after 15 minutes. Try splitting the PDF into smaller parts.');
            } else {
                showError(err.message);
            }
        } finally {
            // Restore UI
            extractBtn.disabled = false;
            btnText.textContent = 'Extract Text';
            btnText.classList.remove('hidden');
            spinner.classList.add('hidden');
        }
    });

    // --- Result Handling ---
    
    function showResult(data) {
        resultText.value = data.text || '';
        resultSection.classList.remove('hidden');
        
        // Build Meta panel
        metaPanel.innerHTML = '';
        if (data.meta) {
            const m = data.meta;
            if (m.pages) addMetaItem('Pages', m.pages);
            if (m.method_per_page) {
                const ocrCount = m.method_per_page.filter(x => x === 'image_ocr' || x === 'ocr').length;
                addMetaItem('Extraction Method', `Image OCR on all ${ocrCount} page(s)`);
            } else if (m.method) {
                const label = m.method === 'image_ocr' ? 'Image OCR' : m.method.toUpperCase();
                addMetaItem('Extraction Method', label);
            }
            if (m.mean_confidence) addMetaItem('OCR Confidence', `${m.mean_confidence.toFixed(1)}%`);
        }
        
        // Scroll to results
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

    // --- Actions ---
    
    copyBtn.addEventListener('click', () => {
        if (!resultText.value) return;
        navigator.clipboard.writeText(resultText.value)
            .then(() => {
                const original = copyBtn.textContent;
                copyBtn.textContent = 'Copied!';
                setTimeout(() => copyBtn.textContent = original, 2000);
            })
            .catch(() => alert('Failed to copy text.'));
    });

    downloadBtn.addEventListener('click', () => {
        if (!resultText.value) return;
        const blob = new Blob([resultText.value], { type: 'text/plain;charset=utf-8' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        const baseName = currentFile ? currentFile.name.replace(/\.[^/.]+$/, "") : "extracted";
        a.download = `${baseName}_extracted.txt`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    });
});
