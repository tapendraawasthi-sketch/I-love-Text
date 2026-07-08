document.addEventListener('DOMContentLoaded', () => {
    const uploadZone = document.getElementById('upload-zone');
    const fileInput = document.getElementById('file-input');
    const fileInfo = document.getElementById('file-info');
    const fileName = document.getElementById('file-name');
    const fileSize = document.getElementById('file-size');

    const extractBtn = document.getElementById('extract-btn');
    const convertPdfBtn = document.getElementById('convert-pdf-btn');
    const aiConvertBtn = document.getElementById('ai-convert-btn');
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
        
        // Convert to Image PDF only works for PDF/DOCX
        const ext = currentFile ? getExt(currentFile) : '';
        const canConvert = currentFile && (ext === '.pdf' || ext === '.docx');
        convertPdfBtn.disabled = !canConvert;

        // Intelligent PDF Converter only works on PDFs
        aiConvertBtn.disabled = !(currentFile && ext === '.pdf');
        
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

    function safePdfDownloadName(originalName) {
        const base = originalName.replace(/\.[^/.]+$/, '');
        const ascii = base.replace(/[^\w.\- ]+/g, '_').replace(/_+/g, '_').replace(/^_|_$/g, '');
        const stamp = Date.now();
        return `${ascii || 'document'}_image_${stamp}.pdf`;
    }

    // Convert to Image PDF (runs in browser — same as OCR)
    convertPdfBtn.addEventListener('click', async () => {
        if (!currentFile) return;
        
        const ext = getExt(currentFile);
        if (ext !== '.pdf' && ext !== '.docx') {
            showError('Only PDF and DOCX files can be converted to image PDF.');
            return;
        }

        if (!window.TextExtractOCR?.isImagePdfAvailable?.()) {
            showError('PDF conversion libraries not loaded. Check your internet connection and refresh.');
            return;
        }

        hideError();
        convertPdfBtn.disabled = true;
        extractBtn.disabled = true;
        const originalText = convertPdfBtn.textContent;
        convertPdfBtn.textContent = 'Converting...';
        showProgress();

        const onProgress = (done, total, label) => setProgress(done, total, label);

        try {
            const result = await window.TextExtractOCR.convertToImagePdf(
                currentFile,
                onProgress,
                { jpegQuality: 0.92 },
            );

            setProgress(result.meta.pages, result.meta.pages, 'Download ready!');

            const url = URL.createObjectURL(result.blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = safePdfDownloadName(currentFile.name);
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);

            showConversionSuccess(result.meta);

        } catch (err) {
            showError(err.message || 'Conversion failed.');
        } finally {
            convertPdfBtn.disabled = false;
            extractBtn.disabled = !currentFile;
            convertPdfBtn.textContent = originalText;
            hideProgress();
        }
    });

    function showConversionSuccess(meta) {
        resultSection.classList.remove('hidden');
        resultText.value = `✓ Image-only PDF created successfully!\n\nPages: ${meta.pages || 'N/A'}\nInput size: ${meta.input_size_mb || 'N/A'} MB\nOutput size: ${meta.output_size_mb || 'N/A'} MB\n\nThis PDF contains only page images — text cannot be selected or copied.\nUpload it to ChatGPT, Claude, or Gemini for better text understanding.`;
        metaPanel.innerHTML = '';
        addMetaItem('Format', 'Image-only PDF (JPEG pages, no text layer)');
        addMetaItem('Processing', 'In your browser');
        addMetaItem('Pages converted', meta.pages || 'N/A');
        addMetaItem('Input size', (meta.input_size_mb || 'N/A') + ' MB');
        addMetaItem('Output size', (meta.output_size_mb || 'N/A') + ' MB');
        addMetaItem('Render DPI', (meta.dpi || 'N/A') + ' (high quality)');
        if (meta.embedded_image_bytes) {
            addMetaItem('Embedded images', `${Math.round(meta.embedded_image_bytes / 1024)} KB total`);
        }
        resultSection.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }

    extractBtn.addEventListener('click', async () => {
        if (!currentFile) return;

        hideError();
        resultSection.classList.add('hidden');
        extractBtn.disabled = true;
        convertPdfBtn.disabled = true;
        spinner.classList.remove('hidden');
        showProgress();

        const lang = langSelect.value;
        const ext = getExt(currentFile);
        const onProgress = (done, total, label) => setProgress(done, total, label);

        btnText.textContent = 'Processing (pure OCR)…';

        try {
            // All files use browser OCR for maximum quality
            const result = await extractViaBrowser(currentFile, lang, onProgress);
            showResult(result);
        } catch (err) {
            showError(err.message || 'Extraction failed.');
        } finally {
            extractBtn.disabled = false;
            convertPdfBtn.disabled = !currentFile || !['.pdf', '.docx'].includes(getExt(currentFile));
            aiConvertBtn.disabled = !(currentFile && getExt(currentFile) === '.pdf');
            btnText.textContent = 'Extract Text (Max Quality)';
            spinner.classList.add('hidden');
            hideProgress();
        }
    });

    // ── Intelligent PDF Converter ─────────────────────────────────────────
    const aiSpinner   = aiConvertBtn.querySelector('.ai-spinner');
    const aiText      = aiConvertBtn.querySelector('.ai-btn-text');
    const aiIcon      = aiConvertBtn.querySelector('.ai-btn-icon');

    aiConvertBtn.addEventListener('click', async () => {
        if (!currentFile || getExt(currentFile) !== '.pdf') return;

        hideError();
        resultSection.classList.add('hidden');
        extractBtn.disabled = true;
        convertPdfBtn.disabled = true;
        aiConvertBtn.disabled = true;

        aiIcon.classList.add('hidden');
        aiSpinner.classList.remove('hidden');
        aiText.textContent = 'Detecting font & converting…';
        showProgress();
        setProgress(10, 100, 'Step 1 — Detecting fonts in document…');

        try {
            const formData = new FormData();
            formData.append('file', currentFile);
            formData.append('model', 'llama3');

            setProgress(30, 100, 'Step 2 — Converting font encoding to Unicode…');

            const resp = await fetch('/api/extract/smart-txt', {
                method: 'POST',
                body: formData,
            });

            setProgress(60, 100, 'Step 3 — AI Actor correcting text meaning…');

            if (!resp.ok) {
                let detail = `Server error ${resp.status}`;
                try {
                    const err = await resp.json();
                    detail = err.detail || err.error || detail;
                } catch (_) {}
                throw new Error(detail);
            }

            // Read response headers for font metadata
            const fontStrategy  = resp.headers.get('X-Font-Strategy') || 'unknown';
            const dominantFont  = resp.headers.get('X-Dominant-Font') || 'unknown';
            const aiApplied     = resp.headers.get('X-AI-Applied') === 'true';
            const pages         = resp.headers.get('X-Pages') || '?';
            const aiIterations  = resp.headers.get('X-AI-Iterations') || '?';

            // Download the .txt blob
            const blob = await resp.blob();
            setProgress(100, 100, 'Done! Downloading…');

            const url = URL.createObjectURL(blob);
            const a   = document.createElement('a');
            a.href    = url;
            const baseName = currentFile.name.replace(/\.[^/.]+$/, '');
            a.download = `${baseName}_ai_corrected.txt`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);

            // Also show the text in the result area
            const text = await new Response(blob.slice()).text();

            resultText.value = text;
            resultSection.classList.remove('hidden');
            metaPanel.innerHTML = '';

            // AI badge
            const badge = document.createElement('div');
            badge.innerHTML = `<span class="ai-badge">✦ Intelligent PDF Converter</span>`;
            metaPanel.appendChild(badge);

            addMetaItem('Pages', pages);
            addMetaItem('Font detected', dominantFont.toUpperCase());
            addMetaItem('Strategy', fontStrategy.replace(/_/g, ' '));
            addMetaItem('AI correction', aiApplied ? `✓ Applied (${aiIterations} review rounds)` : 'Skipped (already clean)');
            addMetaItem('Output', 'Downloaded as .txt');

            resultSection.scrollIntoView({ behavior: 'smooth', block: 'start' });

        } catch (err) {
            showError(err.message || 'Intelligent conversion failed.');
        } finally {
            extractBtn.disabled = false;
            convertPdfBtn.disabled = !currentFile || !['.pdf', '.docx'].includes(getExt(currentFile));
            aiConvertBtn.disabled = !(currentFile && getExt(currentFile) === '.pdf');
            aiIcon.classList.remove('hidden');
            aiSpinner.classList.add('hidden');
            aiText.textContent = 'Intelligent PDF Converter';
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
            
            addMetaItem('Method', m.method || 'Adaptive OCR');
            addMetaItem('Processing', 'In your browser');
            
            if (m.device_profile) {
                addMetaItem('Device mode', m.device_profile);
            }
            
            if (m.workers) {
                addMetaItem('Parallel workers', m.workers);
            }
            
            if (m.fast_path_pages !== undefined && m.slow_path_pages !== undefined) {
                addMetaItem('Fast path pages', `${m.fast_path_pages} (easy)`);
                addMetaItem('Multi-pass pages', `${m.slow_path_pages} (needed extra processing)`);
            }
            
            if (m.render_scale) {
                const dpi = Math.round(m.render_scale * 72);
                addMetaItem('Render quality', `${m.render_scale}× scale (~${dpi} DPI)`);
            }
            
            if (m.mean_confidence !== undefined) {
                let confLabel = `${m.mean_confidence}%`;
                if (m.min_confidence !== undefined && m.max_confidence !== undefined) {
                    confLabel += ` (range: ${m.min_confidence}%-${m.max_confidence}%)`;
                }
                addMetaItem('OCR Confidence', confLabel);
            }
            
            if (m.total_nepali_chars) {
                addMetaItem('Nepali characters', m.total_nepali_chars);
            }
            
            if (m.variant_used) {
                addMetaItem('Best variant', m.variant_used);
            }
            
            if (m.preprocessing) {
                const pp = m.preprocessing;
                const features = [];
                if (pp.background_removal) features.push('background removal');
                if (pp.local_adaptive_threshold) features.push('local adaptive threshold');
                if (pp.noise_reduction) features.push('noise reduction');
                if (pp.morphological_cleanup) features.push('morphological cleanup');
                if (pp.adaptive_multipass) features.push('adaptive multi-pass');
                if (features.length) {
                    addMetaItem('Preprocessing', features.join(', '));
                }
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
