"""
Main FastAPI application.
"""
import os
import re
from urllib.parse import quote

from fastapi import FastAPI, UploadFile, File, Form, Request
from fastapi.responses import JSONResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from app.config import ALLOWED_EXTENSIONS, MAX_FILE_SIZE_MB, configure_tesseract
from app.ocr.engine import available_languages
from app.extract.pdf_handler import extract_pdf
from app.extract.docx_handler import extract_docx
from app.extract.image_handler import extract_image
from app.extract.raster_pipeline import convert_to_image_pdf
from app.logging_config import get_logger
from app.nlp.intent_classifier import classify
from app.extract.txt_formatter import format_as_txt

logger = get_logger("TextExtract")

app = FastAPI(title="TextExtract", version="2.1.0")


def attachment_disposition(filename: str) -> str:
    """Build a Content-Disposition header safe for non-ASCII filenames
    (e.g. Nepali), preserving whatever extension `filename` actually has
    rather than assuming PDF."""
    stem, ext = os.path.splitext(filename)
    ascii_stem = stem.encode("ascii", "ignore").decode().strip()
    ascii_stem = "".join(
        c if c.isalnum() or c in "._- " else "_" for c in ascii_stem
    ).strip("._- ") or "document"
    ascii_fallback = ascii_stem + (ext if ext else "")
    return (
        f'attachment; filename="{ascii_fallback}"; '
        f"filename*=UTF-8''{quote(filename)}"
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,  # no cookie/session auth exists; credentials
    # would only widen the blast radius of the open origin policy above
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup_event():
    configure_tesseract()
    langs = available_languages()
    logger.info(f"Tesseract languages: {langs}")
    if "nep" not in langs:
        logger.warning("'nep' language pack NOT installed. Nepali OCR will fail.")
    
    # Check npttf2utf
    from app.legacy_fonts.converter import check_conversion_status
    conv_status = check_conversion_status()
    if conv_status.get("npttf2utf_working"):
        logger.info("npttf2utf working — legacy font conversion available")
    else:
        logger.warning("npttf2utf NOT working. Using built-in Preeti map only.")


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"success": False, "detail": "Internal server error"},
    )


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError):
    return JSONResponse(status_code=400, content={"success": False, "detail": str(exc)})


@app.get("/api/health")
async def health_check():
    langs = available_languages()
    
    # Check legacy font conversion
    from app.legacy_fonts.converter import check_conversion_status
    conversion_status = check_conversion_status()
    
    return {
        "status": "ok",
        "languages": langs,
        "legacy_font_support": True,  # Always true now with built-in
        "conversion": conversion_status,
    }


@app.post("/api/extract")
async def extract_api(
    file: UploadFile = File(...),
    lang: str = Form("auto"),
    mode: str = Form("auto"),
):
    """
    Extract text from document.
    
    Modes:
        - "direct": Text layer extraction only (fastest, 95-100% accuracy for digital PDFs)
        - "ocr": Image OCR only (for scanned documents)
        - "auto": Try direct first, OCR fallback for pages without text
    """
    if lang not in ("auto", "eng", "nep", "eng+nep"):
        raise ValueError(f"Invalid language: {lang}")
    
    if mode not in ("auto", "direct", "ocr"):
        raise ValueError(f"Invalid mode: {mode}. Use 'auto', 'direct', or 'ocr'.")

    filename = file.filename or "unknown"
    ext = os.path.splitext(filename)[1].lower()

    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(f"File type {ext} not allowed. Supported: {', '.join(ALLOWED_EXTENSIONS)}")

    file_bytes = await file.read()
    size_mb = len(file_bytes) / (1024 * 1024)

    if size_mb > MAX_FILE_SIZE_MB:
        raise ValueError(f"File ({size_mb:.1f}MB) exceeds {MAX_FILE_SIZE_MB}MB limit.")

    logger.info(f"Processing: {filename} ({size_mb:.2f}MB), lang={lang}, mode={mode}")

    try:
        if ext == ".pdf":
            result = await run_in_threadpool(extract_pdf, file_bytes, lang, mode)
        elif ext == ".docx":
            result = await run_in_threadpool(extract_docx, file_bytes, lang, mode)
        else:
            # Images always use OCR
            result = await run_in_threadpool(extract_image, file_bytes, lang)
    except MemoryError:
        logger.error("OCR ran out of memory for %s", filename, exc_info=True)
        return JSONResponse(
            status_code=507,
            content={
                "success": False,
                "detail": (
                    "Server ran out of memory while processing this file. "
                    "Try a smaller PDF, fewer pages, or lower-resolution scan."
                ),
            },
        )

    text = result.pop("text", "")

    return {
        "success": True,
        "text": text,
        "filename": filename,
        "lang": lang,
        "mode": mode,
        "meta": result,
    }


@app.post("/api/extract-txt")
async def extract_txt_api(
    file: UploadFile = File(...),
    lang: str = Form("auto"),
    page_separators: bool = Form(True),
    headers_footers: bool = Form(True),
    quality_report: bool = Form(False),
):
    """
    Extract text from a document and return a clean .txt file download.

    The response is a UTF-8 plain-text file (Content-Type: text/plain).
    Nepali text is fully converted to Unicode — no legacy font encoding.

    Form parameters
    ---------------
    file            : The PDF file to extract.
    lang            : "auto" | "eng" | "nep" | "eng+nep"
    page_separators : Include ═══ PAGE N ═══ separators (default true).
    headers_footers : Include [HEADER] and [FOOTER] labels (default true).
    quality_report  : Append extraction quality summary at end (default false).
    """
    filename = file.filename or "document"
    ext = os.path.splitext(filename)[1].lower()

    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(f"File type {ext} not allowed.")

    file_bytes = await file.read()
    size_mb = len(file_bytes) / (1024 * 1024)

    if size_mb > MAX_FILE_SIZE_MB:
        raise ValueError(f"File ({size_mb:.1f}MB) exceeds {MAX_FILE_SIZE_MB}MB limit.")

    logger.info("extract-txt: %s (%.2f MB), lang=%s", filename, size_mb, lang)

    if ext == ".pdf":
        result = await run_in_threadpool(extract_pdf, file_bytes, lang, "auto")
    elif ext == ".docx":
        result = await run_in_threadpool(extract_docx, file_bytes, lang, "auto")
    else:
        result = await run_in_threadpool(extract_image, file_bytes, lang)

    txt_content = format_as_txt(
        result,
        include_page_separators=page_separators,
        include_headers_footers=headers_footers,
        include_quality_report=quality_report,
    )

    # Build download filename, preserving non-ASCII (e.g. Nepali) names
    # via the shared RFC 5987-aware helper instead of ASCII-slugifying them
    # away.
    base = os.path.splitext(filename)[0]
    download_name = f"{base}_extracted.txt"

    return Response(
        content=txt_content.encode("utf-8"),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": attachment_disposition(download_name)},
    )


@app.post("/api/convert-to-image-pdf")
async def convert_to_image_pdf_api(
    file: UploadFile = File(...),
    dpi: int = Form(350),
    quality: int = Form(92),
):
    """
    Convert PDF to image-only PDF.
    
    Each page is rendered as a high-quality image and saved into a new PDF.
    The output PDF has no text layer - just images of the pages.
    Perfect for using with AI vision models (ChatGPT, Claude, Gemini).
    
    Args:
        file: PDF or DOCX file
        dpi: Render resolution (default 350, higher = better quality but larger file)
        quality: JPEG quality 1-100 (default 92)
    """
    filename = file.filename or "document"
    ext = os.path.splitext(filename)[1].lower()
    
    if ext not in (".pdf", ".docx"):
        raise ValueError("Only PDF and DOCX files are supported for conversion.")
    
    file_bytes = await file.read()
    size_mb = len(file_bytes) / (1024 * 1024)
    
    if size_mb > MAX_FILE_SIZE_MB:
        raise ValueError(f"File ({size_mb:.1f}MB) exceeds {MAX_FILE_SIZE_MB}MB limit.")
    
    logger.info(f"Converting to image PDF: {filename} ({size_mb:.2f}MB), dpi={dpi}, quality={quality}")
    
    try:
        filetype = "pdf" if ext == ".pdf" else "docx"
        pdf_bytes, meta = await run_in_threadpool(
            convert_to_image_pdf, 
            file_bytes, 
            filetype,
            dpi,
            quality,
        )
    except MemoryError:
        logger.error("Conversion ran out of memory for %s", filename, exc_info=True)
        return JSONResponse(
            status_code=507,
            content={
                "success": False,
                "detail": "Server ran out of memory. Try a smaller file or lower DPI.",
            },
        )
    
    # Generate output filename
    base_name = os.path.splitext(filename)[0]
    output_filename = f"{base_name}_image.pdf"
    
    logger.info(f"Conversion complete: {meta['pages']} pages, {meta['output_size_mb']}MB")
    
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": attachment_disposition(output_filename),
            "X-Pages": str(meta["pages"]),
            "X-DPI": str(meta["dpi"]),
            "X-Output-Size-MB": str(meta["output_size_mb"]),
        },
    )


@app.post("/api/classify")
async def classify_intent_api(text: str = Form(...)):
    """
    Classify the intent of a given text string using ERP intelligence.
    """
    if not text or not text.strip():
        raise ValueError("Text cannot be empty.")
        
    intent = classify(text)
    
    logger.info(f"Classified intent: '{intent}' for text: '{text[:50]}...'")
    
    return {
        "success": True,
        "text": text,
        "intent": intent
    }


@app.post("/api/detect-fonts")
async def detect_fonts_api(
    file: UploadFile = File(...),
):
    """
    Analyse a PDF and report which fonts are used (Preeti, Kantipur,
    Sagarmatha, Unicode, etc.) along with a recommended conversion strategy.
    """
    filename = file.filename or "unknown.pdf"
    ext = os.path.splitext(filename)[1].lower()
    if ext != ".pdf":
        raise ValueError("Only PDF files are supported.")

    file_bytes = await file.read()
    size_mb = len(file_bytes) / (1024 * 1024)
    if size_mb > MAX_FILE_SIZE_MB:
        raise ValueError(f"File ({size_mb:.1f}MB) exceeds {MAX_FILE_SIZE_MB}MB limit.")

    try:
        from app.nlp.font_detector import analyse_document_fonts
        result = await run_in_threadpool(analyse_document_fonts, file_bytes)
    except Exception as e:
        logger.error(f"Font detection error: {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"success": False, "detail": "Font detection failed", "error": str(e)},
        )

    return {"success": True, "filename": filename, **result}


@app.post("/api/extract/smart-txt")
async def extract_pdf_smart_txt_api(
    file: UploadFile = File(...),
    model: str = Form("llama3"),
    use_ai: bool = Form(False),
):
    """
    High-accuracy font-aware PDF → TXT (no OCR).

    Reads the PDF text layer directly and converts legacy Nepali fonts
    (Preeti, Kantipur, Sagarmatha, etc.) to proper Unicode. This is more
    accurate than OCR for PDFs that already contain embedded text.

    Optional AI refinement is off by default and only runs when use_ai=true
    and ENABLE_LLM_OCR_ENHANCEMENT is enabled in the environment.
    """
    filename = file.filename or "unknown.pdf"
    ext = os.path.splitext(filename)[1].lower()

    if ext != ".pdf":
        raise ValueError("Only PDF files are supported for this endpoint.")

    file_bytes = await file.read()
    size_mb = len(file_bytes) / (1024 * 1024)
    if size_mb > MAX_FILE_SIZE_MB:
        raise ValueError(f"File ({size_mb:.1f}MB) exceeds {MAX_FILE_SIZE_MB}MB limit.")

    logger.info(
        f"Smart-TXT pipeline: {filename} ({size_mb:.2f}MB), model={model}, use_ai={use_ai}"
    )

    try:
        from app.nlp.ai_corrector import process_pdf_smart
        result = await run_in_threadpool(
            process_pdf_smart, file_bytes, model, use_ai=use_ai
        )
    except MemoryError:
        return JSONResponse(
            status_code=507,
            content={"success": False, "detail": "Out of memory. Try a smaller PDF."},
        )
    except Exception as e:
        logger.error(f"Smart-TXT pipeline error: {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"success": False, "detail": "Pipeline failed", "error": str(e)},
        )

    base_name = os.path.splitext(filename)[0]
    output_filename = f"{base_name}_unicode.txt"

    font_summary = result["font_analysis"].get("summary", "")
    logger.info(f"Smart-TXT complete — {font_summary}")

    headers = {
        "Content-Disposition": attachment_disposition(output_filename),
        "X-Pages": str(result["pages"]),
        "X-Font-Strategy": result["font_analysis"].get("strategy", "unknown"),
        "X-Dominant-Font": result["font_analysis"].get("dominant_family", "unknown"),
        "X-AI-Applied": str(result["ai_applied"]).lower(),
        "X-AI-Iterations": str(result.get("iterations", 0)),
        "X-Confidence": str(result.get("confidence", 0)),
        "X-Quality-Score": str(result.get("quality", {}).get("score", 0)),
        "X-Method": result.get("method", "direct_font_conversion"),
        "X-Tables-Detected": str(result.get("tables_detected", 0)),
        "X-Tables-Borderless": str(result.get("tables_by_method", {}).get("column_clustering", 0)),
    }
    if result.get("ai_skipped_reason"):
        headers["X-AI-Skipped-Reason"] = result["ai_skipped_reason"]

    return Response(
        content=result["text"].encode("utf-8"),
        media_type="text/plain; charset=utf-8",
        headers=headers,
    )


# Mount frontend last
frontend_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend")
if os.path.exists(frontend_dir):
    app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")
