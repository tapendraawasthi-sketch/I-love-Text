# TextExtract

TextExtract is a high-accuracy text-extraction web app backend. 
It extracts human-readable text from PDFs, DOCX, and images in both English and Nepali. 
This project uses strictly classical OCR (Tesseract) and rule-based legacy-font (Preeti/Kantipur/etc.) to Unicode conversion without any AI/LLM/cloud recognition components.

## System Dependencies

This application requires the following system packages to be installed on the host machine:
- `tesseract-ocr`
- `tesseract-ocr-nep`
- `tesseract-ocr-eng`
- `poppler-utils`

*(These are also listed in `packages.txt` for deployment platforms that support automatic installation.)*

## Running Locally

1. Create and activate a Python 3.11+ virtual environment.
2. Install the python dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Start the application:
   ```bash
   uvicorn app.main:app --reload
   ```

## API Endpoint Contract

**POST `/api/extract`**
Extracts text from the uploaded file.

- **Payload:** `multipart/form-data`
  - `file`: The file to be extracted (PDF, DOCX, PNG, JPG, JPEG, TIFF, BMP, WEBP).
  - `lang` *(optional)*: Language to use for OCR. Valid options: `auto`, `eng`, `nep`, `eng+nep`. Defaults to `auto`.

- **Response:**
  Returns a JSON payload with the extracted text.

## Docker Deployment

To build and run TextExtract with Docker (which automatically handles system dependencies like Tesseract and Poppler):

```bash
docker-compose up --build
```
The application will be available at `http://localhost:8000`.

### Verifying Nepali OCR Support
To verify that the `nep` language pack is successfully installed in the container:
```bash
docker exec <container_name_or_id> tesseract --list-langs
```
You should see `nep` in the output list.

## API Schema & Examples

### Endpoint: `POST /api/extract`

**cURL Example (Image, Auto Language):**
```bash
curl -X POST http://localhost:8000/api/extract \
  -F 'file=@/path/to/scan.jpg' \
  -F 'lang=auto'
```

**cURL Example (Preeti PDF, Nepali):**
```bash
curl -X POST http://localhost:8000/api/extract \
  -F 'file=@/path/to/document.pdf' \
  -F 'lang=nep'
```

**Response Schema:**
```json
{
  "success": true,
  "text": "Extracted unicode text here...",
  "filename": "scan.jpg",
  "lang": "auto",
  "meta": {
    "method": "ocr",
    "mean_confidence": 85.5
  }
}
```

## Extending Legacy Fonts
To add support for other legacy fonts like Kantipur or Sagarmatha, simply fill their respective maps in `app/legacy_fonts/mappings.py`. Follow the same three-stage structure (Pre-rules, Char-map, Post-rules) as the `PREETI_MAP`.
