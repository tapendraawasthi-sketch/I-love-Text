# ZipScan Pro 🔍

> **Advanced OCR & ZIP Image Text Extractor** — 100% browser-based, no backend required.

[![Live Demo](https://img.shields.io/badge/Live-Demo-6366F1?style=for-the-badge)](https://localhost:3030)

## Features

- 📦 **ZIP Upload up to 500 MB** — drag & drop or browse
- 🖼️ **Multi-format image support** — JPG, PNG, TIFF, BMP, WebP, GIF, HEIC & more
- 🔤 **Advanced OCR** powered by Tesseract.js v5 (12+ languages)
- ⚡ **Multi-worker parallel processing** — up to 8 concurrent OCR workers
- 🎨 **Image pre-processing** — grayscale conversion + sharpen kernel for better accuracy
- 📊 **Confidence scores** per image
- 🔍 **Real-time search & filter** across all extracted text
- 📤 **Export** results as TXT, JSON, or CSV
- 🌙 **Premium dark UI** with glassmorphism & animations

## Getting Started

```bash
# Serve locally (no build step needed)
npx serve . --listen 3030
```

Then open [http://localhost:3030](http://localhost:3030) in your browser.

## Tech Stack

| Library | Purpose |
|---|---|
| [JSZip v3](https://stuk.github.io/jszip/) | ZIP extraction in the browser |
| [Tesseract.js v5](https://tesseract.projectnaptha.com/) | In-browser OCR engine |
| Vanilla HTML/CSS/JS | No framework needed |

## Usage

1. Upload a `.zip` file containing images
2. Configure OCR language, mode, and quality settings
3. Click **Browse Files** or drag & drop your ZIP
4. Watch real-time processing with live queue
5. Browse results, preview images, copy or export text

## License

MIT
