document.addEventListener('DOMContentLoaded', () => {
    const MAX_MB = 100;

    const errorBanner = document.getElementById('error-banner');
    const errorMessage = document.getElementById('error-message');

    function showError(msg) {
        errorMessage.textContent = msg;
        errorBanner.classList.remove('hidden');
        errorBanner.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }

    function hideError() {
        errorBanner.classList.add('hidden');
    }

    function getExt(file) {
        return '.' + file.name.split('.').pop().toLowerCase();
    }

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

    // ------------------------------------------------------------------
    // Shared helpers used by both sections
    // ------------------------------------------------------------------

    function wireUploadZone({ zoneEl, inputEl, infoEl, nameEl, sizeEl, allowedExts, onFileChosen }) {
        function handleFile(file) {
            hideError();
            const sizeMb = file.size / (1024 * 1024);
            if (sizeMb > MAX_MB) {
                showError(`File is too large (${sizeMb.toFixed(1)}MB). Max allowed is ${MAX_MB}MB.`);
                onFileChosen(null);
                return;
            }
            const ext = getExt(file);
            if (!allowedExts.includes(ext)) {
                showError(`File type ${ext} not supported here.`);
                onFileChosen(null);
                return;
            }
            nameEl.textContent = file.name;
            sizeEl.textContent = `${sizeMb.toFixed(2)} MB`;
            zoneEl.querySelector('.upload-content').classList.add('hidden');
            infoEl.classList.remove('hidden');
            onFileChosen(file);
        }

        zoneEl.addEventListener('click', () => inputEl.click());
        zoneEl.addEventListener('dragover', (e) => { e.preventDefault(); zoneEl.classList.add('dragover'); });
        zoneEl.addEventListener('dragleave', () => zoneEl.classList.remove('dragover'));
        zoneEl.addEventListener('drop', (e) => {
            e.preventDefault();
            zoneEl.classList.remove('dragover');
            if (e.dataTransfer.files.length) handleFile(e.dataTransfer.files[0]);
        });
        inputEl.addEventListener('change', () => {
            if (inputEl.files.length) handleFile(inputEl.files[0]);
        });

        return {
            reset() {
                zoneEl.querySelector('.upload-content').classList.remove('hidden');
                infoEl.classList.add('hidden');
            },
        };
    }

    function setProgress(fillEl, labelEl, done, total, label) {
        const pct = total ? Math.round((done / total) * 100) : 0;
        fillEl.style.width = `${pct}%`;
        labelEl.textContent = label || `${pct}%`;
    }

    function addMetaItem(panelEl, label, value, confidencePct) {
        const div = document.createElement('div');
        div.className = 'meta-item';
        let dot = '';
        if (typeof confidencePct === 'number' && !Number.isNaN(confidencePct)) {
            const tier = confidencePct >= 85 ? 'conf-high' : confidencePct >= 60 ? 'conf-mid' : 'conf-low';
            dot = `<span class="conf-dot ${tier}" title="${confidencePct}% confidence"></span>`;
        }
        div.innerHTML = `${dot}<strong>${label}:</strong> ${value}`;
        panelEl.appendChild(div);
    }

    function parsePercent(value) {
        const n = parseFloat(value);
        return Number.isNaN(n) ? null : n;
    }

    function wireCopyDownload({ copyBtn, downloadBtn, textareaEl, getFileBaseName, suffix }) {
        copyBtn.addEventListener('click', () => {
            if (!textareaEl.value) return;
            navigator.clipboard.writeText(textareaEl.value)
                .then(() => {
                    const original = copyBtn.textContent;
                    copyBtn.textContent = 'Copied!';
                    setTimeout(() => { copyBtn.textContent = original; }, 2000);
                })
                .catch(() => alert('Failed to copy text.'));
        });

        downloadBtn.addEventListener('click', () => {
            if (!textareaEl.value) return;
            const blob = new Blob([textareaEl.value], { type: 'text/plain;charset=utf-8' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `${getFileBaseName() || 'extracted'}${suffix}`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
        });
    }

    // ------------------------------------------------------------------
    // Conversion history — last 5 results per section, stored locally in
    // the browser only (never sent anywhere). Lets someone processing
    // several documents in one sitting get back a previous result after
    // switching files, without needing to re-run extraction.
    // ------------------------------------------------------------------
    const HISTORY_LIMIT = 5;

    function wireHistory({ storageKey, panelEl, toggleBtn, listEl, countEl, textareaEl, resultSectionEl }) {
        function load() {
            try {
                const raw = localStorage.getItem(storageKey);
                return raw ? JSON.parse(raw) : [];
            } catch (_) {
                return [];
            }
        }

        function save(entries) {
            try {
                localStorage.setItem(storageKey, JSON.stringify(entries.slice(0, HISTORY_LIMIT)));
            } catch (_) { /* storage full or unavailable — history is a nice-to-have, fail silently */ }
        }

        function render() {
            const entries = load();
            listEl.innerHTML = '';
            if (!entries.length) {
                panelEl.classList.add('hidden');
                return;
            }
            panelEl.classList.remove('hidden');
            countEl.textContent = `(${entries.length})`;
            entries.forEach((entry, i) => {
                const li = document.createElement('li');
                const when = new Date(entry.ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
                li.innerHTML = `<span class="history-name">${entry.name}</span><span class="history-time">${when}</span>`;
                li.title = 'Click to restore this result';
                li.addEventListener('click', () => {
                    textareaEl.value = entry.text;
                    resultSectionEl.classList.remove('hidden');
                    resultSectionEl.scrollIntoView({ behavior: 'smooth', block: 'start' });
                });
                listEl.appendChild(li);
            });
        }

        toggleBtn.addEventListener('click', () => {
            listEl.classList.toggle('hidden');
            toggleBtn.classList.toggle('expanded');
        });

        render();

        return {
            add(name, text) {
                if (!text) return;
                const entries = load();
                entries.unshift({ name, text, ts: Date.now() });
                save(entries);
                render();
            },
        };
    }

    // ==================================================================
    // SECTION A — Scan / Image OCR (client-side, Tesseract.js)
    // ==================================================================
    (function initOcrSection() {
        const uploadZone = document.getElementById('upload-zone-ocr');
        const fileInput = document.getElementById('file-input-ocr');
        const fileInfo = document.getElementById('file-info-ocr');
        const fileNameEl = document.getElementById('file-name-ocr');
        const fileSizeEl = document.getElementById('file-size-ocr');

        const extractBtn = document.getElementById('extract-btn');
        const convertPdfBtn = document.getElementById('convert-pdf-btn');
        const langSelect = document.getElementById('lang-select');
        const btnText = extractBtn.querySelector('.btn-text');
        const spinner = extractBtn.querySelector('.spinner');

        const resultSection = document.getElementById('result-section-ocr');
        const resultText = document.getElementById('result-text-ocr');
        const metaPanel = document.getElementById('meta-panel-ocr');
        const copyBtn = document.getElementById('copy-btn-ocr');
        const downloadBtn = document.getElementById('download-btn-ocr');

        const progressSection = document.getElementById('progress-section-ocr');
        const progressFill = document.getElementById('progress-fill-ocr');
        const progressLabel = document.getElementById('progress-label-ocr');

        const history = wireHistory({
            storageKey: 'textextract_history_ocr',
            panelEl: document.getElementById('history-panel-ocr'),
            toggleBtn: document.getElementById('history-toggle-ocr'),
            listEl: document.getElementById('history-list-ocr'),
            countEl: document.getElementById('history-count-ocr'),
            textareaEl: resultText,
            resultSectionEl: resultSection,
        });

        let currentFile = null;

        function updateButtons() {
            extractBtn.disabled = !currentFile;
            const ext = currentFile ? getExt(currentFile) : '';
            convertPdfBtn.disabled = !(currentFile && (ext === '.pdf' || ext === '.docx'));
        }

        const uploadCtl = wireUploadZone({
            zoneEl: uploadZone,
            inputEl: fileInput,
            infoEl: fileInfo,
            nameEl: fileNameEl,
            sizeEl: fileSizeEl,
            allowedExts: ['.pdf', '.docx', '.png', '.jpg', '.jpeg', '.tiff', '.bmp', '.webp'],
            onFileChosen(file) {
                currentFile = file;
                updateButtons();
                if (!file) uploadCtl?.reset();
            },
        });

        function showProgress() {
            progressSection.classList.remove('hidden');
            setProgress(progressFill, progressLabel, 0, 100, 'Starting…');
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
            return { text: result.text, meta: { ...result.meta, processed_locally: true } };
        }

        function safePdfDownloadName(originalName) {
            const base = originalName.replace(/\.[^/.]+$/, '');
            const ascii = base.replace(/[^\w.\- ]+/g, '_').replace(/_+/g, '_').replace(/^_|_$/g, '');
            const stamp = Date.now();
            return `${ascii || 'document'}_image_${stamp}.pdf`;
        }

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

            const onProgress = (done, total, label) => setProgress(progressFill, progressLabel, done, total, label);

            try {
                const result = await window.TextExtractOCR.convertToImagePdf(currentFile, onProgress, { jpegQuality: 0.92 });
                setProgress(progressFill, progressLabel, result.meta.pages, result.meta.pages, 'Download ready!');

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
            addMetaItem(metaPanel, 'Format', 'Image-only PDF (JPEG pages, no text layer)');
            addMetaItem(metaPanel, 'Processing', 'In your browser');
            addMetaItem(metaPanel, 'Pages converted', meta.pages || 'N/A');
            addMetaItem(metaPanel, 'Input size', (meta.input_size_mb || 'N/A') + ' MB');
            addMetaItem(metaPanel, 'Output size', (meta.output_size_mb || 'N/A') + ' MB');
            addMetaItem(metaPanel, 'Render DPI', (meta.dpi || 'N/A') + ' (high quality)');
            if (meta.embedded_image_bytes) {
                addMetaItem(metaPanel, 'Embedded images', `${Math.round(meta.embedded_image_bytes / 1024)} KB total`);
            }
            resultSection.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }

        function showResult(data) {
            resultText.value = data.text || '';
            resultSection.classList.remove('hidden');
            metaPanel.innerHTML = '';
            if (data.meta) {
                const m = data.meta;
                if (m.pages) addMetaItem(metaPanel, 'Pages processed', m.pages);
                addMetaItem(metaPanel, 'Method', m.method || 'Adaptive OCR');
                addMetaItem(metaPanel, 'Processing', 'In your browser');
                if (m.device_profile) addMetaItem(metaPanel, 'Device mode', m.device_profile);
                if (m.workers) addMetaItem(metaPanel, 'Parallel workers', m.workers);
                if (m.fast_path_pages !== undefined && m.slow_path_pages !== undefined) {
                    addMetaItem(metaPanel, 'Fast path pages', `${m.fast_path_pages} (easy)`);
                    addMetaItem(metaPanel, 'Multi-pass pages', `${m.slow_path_pages} (needed extra processing)`);
                }
                if (m.render_scale) {
                    const dpi = Math.round(m.render_scale * 72);
                    addMetaItem(metaPanel, 'Render quality', `${m.render_scale}× scale (~${dpi} DPI)`);
                }
                if (m.mean_confidence !== undefined) {
                    let confLabel = `${m.mean_confidence}%`;
                    if (m.min_confidence !== undefined && m.max_confidence !== undefined) {
                        confLabel += ` (range: ${m.min_confidence}%-${m.max_confidence}%)`;
                    }
                    addMetaItem(metaPanel, 'OCR Confidence', confLabel, parsePercent(m.mean_confidence));
                }
                if (m.total_nepali_chars) addMetaItem(metaPanel, 'Nepali characters', m.total_nepali_chars);
                if (m.variant_used) addMetaItem(metaPanel, 'Best variant', m.variant_used);
                if (m.preprocessing) {
                    const pp = m.preprocessing;
                    const features = [];
                    if (pp.background_removal) features.push('background removal');
                    if (pp.local_adaptive_threshold) features.push('local adaptive threshold');
                    if (pp.noise_reduction) features.push('noise reduction');
                    if (pp.morphological_cleanup) features.push('morphological cleanup');
                    if (pp.adaptive_multipass) features.push('adaptive multi-pass');
                    if (features.length) addMetaItem(metaPanel, 'Preprocessing', features.join(', '));
                }
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
            const onProgress = (done, total, label) => setProgress(progressFill, progressLabel, done, total, label);
            btnText.textContent = 'Processing (pure OCR)…';

            try {
                const result = await extractViaBrowser(currentFile, lang, onProgress);
                showResult(result);
                history.add(currentFile.name, result.text);
            } catch (err) {
                showError(err.message || 'Extraction failed.');
            } finally {
                extractBtn.disabled = false;
                updateButtons();
                btnText.textContent = 'Extract Text (Max Quality)';
                spinner.classList.add('hidden');
                hideProgress();
            }
        });

        wireCopyDownload({
            copyBtn, downloadBtn, textareaEl: resultText,
            getFileBaseName: () => currentFile ? currentFile.name.replace(/\.[^/.]+$/, '') : 'extracted',
            suffix: '_extracted.txt',
        });
    })();

    // ==================================================================
    // SECTION B — Nepali Font → Unicode Converter (.txt) (backend)
    // ==================================================================
    (function initNepaliSection() {
        const uploadZone = document.getElementById('upload-zone-np');
        const fileInput = document.getElementById('file-input-np');
        const fileInfo = document.getElementById('file-info-np');
        const fileNameEl = document.getElementById('file-name-np');
        const fileSizeEl = document.getElementById('file-size-np');

        const aiConvertBtn = document.getElementById('ai-convert-btn');
        const aiSpinner = aiConvertBtn.querySelector('.ai-spinner');
        const aiText = aiConvertBtn.querySelector('.ai-btn-text');
        const aiIcon = aiConvertBtn.querySelector('.ai-btn-icon');

        const resultSection = document.getElementById('result-section-np');
        const resultSummary = document.getElementById('result-summary-np');
        const resultText = document.getElementById('result-text-np');
        const metaPanel = document.getElementById('meta-panel-np');
        const copyBtn = document.getElementById('copy-btn-np');
        const downloadBtn = document.getElementById('download-btn-np');

        const progressSection = document.getElementById('progress-section-np');
        const progressFill = document.getElementById('progress-fill-np');
        const progressLabel = document.getElementById('progress-label-np');

        const history = wireHistory({
            storageKey: 'textextract_history_np',
            panelEl: document.getElementById('history-panel-np'),
            toggleBtn: document.getElementById('history-toggle-np'),
            listEl: document.getElementById('history-list-np'),
            countEl: document.getElementById('history-count-np'),
            textareaEl: resultText,
            resultSectionEl: resultSection,
        });

        let currentFile = null;

        wireUploadZone({
            zoneEl: uploadZone,
            inputEl: fileInput,
            infoEl: fileInfo,
            nameEl: fileNameEl,
            sizeEl: fileSizeEl,
            allowedExts: ['.pdf'],
            onFileChosen(file) {
                currentFile = file;
                aiConvertBtn.disabled = !file;
            },
        });

        function showProgress() {
            progressSection.classList.remove('hidden');
            setProgress(progressFill, progressLabel, 0, 100, 'Starting…');
        }
        function hideProgress() {
            progressSection.classList.add('hidden');
        }

        aiConvertBtn.addEventListener('click', async () => {
            if (!currentFile || getExt(currentFile) !== '.pdf') return;

            hideError();
            resultSection.classList.add('hidden');
            aiConvertBtn.disabled = true;
            aiIcon.classList.add('hidden');
            aiSpinner.classList.remove('hidden');
            aiText.textContent = 'Detecting font & converting…';
            showProgress();
            setProgress(progressFill, progressLabel, 10, 100, 'Step 1 — Detecting fonts in document…');

            try {
                const formData = new FormData();
                formData.append('file', currentFile);
                formData.append('model', 'llama3');
                formData.append('use_ai', 'false');

                setProgress(progressFill, progressLabel, 40, 100, 'Step 2 — Converting font encoding to Unicode (no OCR)…');

                const resp = await fetch('/api/extract/smart-txt', { method: 'POST', body: formData });

                setProgress(progressFill, progressLabel, 80, 100, 'Step 3 — Finalising Unicode text…');

                if (!resp.ok) {
                    let detail = `Server error ${resp.status}`;
                    try {
                        const err = await resp.json();
                        detail = err.detail || err.error || detail;
                    } catch (_) { /* ignore */ }
                    throw new Error(detail);
                }

                const fontStrategy = resp.headers.get('X-Font-Strategy') || 'unknown';
                const dominantFont = resp.headers.get('X-Dominant-Font') || 'unknown';
                const aiApplied = resp.headers.get('X-AI-Applied') === 'true';
                const pages = resp.headers.get('X-Pages') || '?';
                const aiIterations = resp.headers.get('X-AI-Iterations') || '?';
                const aiSkipped = resp.headers.get('X-AI-Skipped-Reason') || '';
                const confidence = resp.headers.get('X-Confidence') || '?';
                const qualityScore = resp.headers.get('X-Quality-Score') || '?';
                const method = resp.headers.get('X-Method') || 'direct_font_conversion';

                const blob = await resp.blob();
                setProgress(progressFill, progressLabel, 100, 100, 'Done! Downloading…');

                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                const baseName = currentFile.name.replace(/\.[^/.]+$/, '');
                a.download = `${baseName}_unicode.txt`;
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                URL.revokeObjectURL(url);

                const text = await new Response(blob.slice()).text();
                resultText.value = text;
                resultSection.classList.remove('hidden');
                metaPanel.innerHTML = '';

                // Plain-language summary line, ahead of the detailed meta panel.
                const fontLabel = dominantFont && dominantFont !== 'unknown' ? dominantFont.toUpperCase() : 'a detected font';
                const strategyLabel = fontStrategy.replace(/_/g, ' ');
                resultSummary.textContent = `Detected ${fontLabel} text layer — converted directly (${strategyLabel}), no OCR needed.`;
                resultSummary.classList.remove('hidden');

                addMetaItem(metaPanel, 'Pages', pages);
                addMetaItem(metaPanel, 'Font detected', dominantFont.toUpperCase());
                addMetaItem(metaPanel, 'Strategy', strategyLabel);
                addMetaItem(metaPanel, 'Method', method.replace(/_/g, ' '));
                addMetaItem(metaPanel, 'Confidence', `${confidence}%`, parsePercent(confidence));
                addMetaItem(metaPanel, 'Quality score', qualityScore, parsePercent(qualityScore));
                addMetaItem(metaPanel, 'AI correction', aiApplied ? `✓ Applied (${aiIterations} pass)` : (aiSkipped ? `Skipped (${aiSkipped.replace(/_/g, ' ')})` : 'Skipped (mechanical conversion used)'));
                addMetaItem(metaPanel, 'Output', 'Downloaded as .txt');

                history.add(currentFile.name, text);

                resultSection.scrollIntoView({ behavior: 'smooth', block: 'start' });
            } catch (err) {
                showError(err.message || 'Conversion failed.');
            } finally {
                aiConvertBtn.disabled = !currentFile;
                aiIcon.classList.remove('hidden');
                aiSpinner.classList.add('hidden');
                aiText.textContent = 'Convert to Unicode (.txt)';
                hideProgress();
            }
        });

        wireCopyDownload({
            copyBtn, downloadBtn, textareaEl: resultText,
            getFileBaseName: () => currentFile ? currentFile.name.replace(/\.[^/.]+$/, '') : 'converted',
            suffix: '_unicode.txt',
        });
    })();
});
