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

# --- Lightweight abuse protection ---------------------------------------
# This runs as a single worker on a resource-constrained host with no
# request queue, so a handful of concurrent heavy uploads can starve
# everyone else. This is a simple in-memory per-IP sliding window, not a
# distributed limiter -- fine for a single-process deployment, but it
# resets on restart and won't coordinate across multiple workers/replicas
# if this is ever scaled out.
import time
from collections import defaultdict, deque

_RATE_LIMIT_WINDOW_SECONDS = 60
_RATE_LIMIT_MAX_REQUESTS = 20
_RATE_LIMITED_PATH_PREFIXES = ("/api/extract", "/api/convert-to-image-pdf")
_request_log: dict[str, deque] = defaultdict(deque)


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    path = request.url.path
    if any(path.startswith(p) for p in _RATE_LIMITED_PATH_PREFIXES):
        client_ip = request.client.host if request.client else "unknown"
        now = time.monotonic()
        bucket = _request_log[client_ip]
        while bucket and now - bucket[0] > _RATE_LIMIT_WINDOW_SECONDS:
            bucket.popleft()
        if len(bucket) >= _RATE_LIMIT_MAX_REQUESTS:
            return JSONResponse(
                status_code=429,
                content={
                    "success": False,
                    "detail": "Too many requests. Please wait a bit and try again.",
                },
                headers={"Retry-After": str(_RATE_LIMIT_WINDOW_SECONDS)},
            )
        bucket.append(now)
    return await call_next(request)


def _memory_error_response(context: str, detail: str) -> JSONResponse:
    logger.error("%s ran out of memory", context, exc_info=True)
    return JSONResponse(status_code=507, content={"success": False, "detail": detail})


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

    from app.legacy_fonts.converter import check_conversion_status
    conversion_status = check_conversion_status()

    # Actually exercise the built-in converter rather than assuming it works
    from app.legacy_fonts.converter import convert_legacy_text
    builtin_ok = "नेपाल" in convert_legacy_text("g]kfn", "Preeti")

    nep_ok = "nep" in langs
    npttf_ok = bool(conversion_status.get("npttf2utf_working"))
    degraded_reasons: list[str] = []
    if not nep_ok:
        degraded_reasons.append("nep_tessdata_missing")
    if not npttf_ok:
        degraded_reasons.append("npttf2utf_unavailable")
    if not builtin_ok:
        degraded_reasons.append("builtin_preeti_converter_failed")

    return {
        "status": "degraded" if degraded_reasons else "ok",
        "degraded": bool(degraded_reasons),
        "degraded_reasons": degraded_reasons,
        "languages": langs,
        "legacy_font_support": builtin_ok,
        "conversion": conversion_status,
        "default_fidelity": "forensic",
    }


@app.post("/api/segment")
async def segment_document_api(
    file: UploadFile = File(...),
):
    """
    Segment every page into semantic blocks before extraction.

    Returns typed blocks (heading, paragraph, table, figure, etc.) with
    bbox, confidence, reading order, and content flags. No OCR is performed.
    """
    filename = file.filename or "unknown"
    ext = os.path.splitext(filename)[1].lower()

    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(
            f"File type {ext} not allowed. Supported: {', '.join(ALLOWED_EXTENSIONS)}"
        )

    file_bytes = await file.read()
    size_mb = len(file_bytes) / (1024 * 1024)

    if size_mb > MAX_FILE_SIZE_MB:
        raise ValueError(f"File ({size_mb:.1f}MB) exceeds {MAX_FILE_SIZE_MB}MB limit.")

    logger.info("Segment: %s (%.2f MB)", filename, size_mb)

    from app.intelligence.document_intelligence import segment_document_json

    file_type = ext.lstrip(".")
    mime = None
    if file.content_type and file.content_type != "application/octet-stream":
        mime = file.content_type

    report = await run_in_threadpool(
        segment_document_json,
        file_bytes,
        file_type=file_type,
        mime_type=mime,
        filename=filename,
    )

    return {
        "success": True,
        "filename": filename,
        **report,
    }


@app.post("/api/analyze")
async def analyze_document_api(
    file: UploadFile = File(...),
):
    """
    Analyze a document structure without extracting text.

    Returns JSON describing document-level and per-page properties:
    fonts, scripts, regions, layout, scan profile, tables, images, etc.
    """
    filename = file.filename or "unknown"
    ext = os.path.splitext(filename)[1].lower()

    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(
            f"File type {ext} not allowed. Supported: {', '.join(ALLOWED_EXTENSIONS)}"
        )

    file_bytes = await file.read()
    size_mb = len(file_bytes) / (1024 * 1024)

    if size_mb > MAX_FILE_SIZE_MB:
        raise ValueError(f"File ({size_mb:.1f}MB) exceeds {MAX_FILE_SIZE_MB}MB limit.")

    logger.info("Analyze: %s (%.2f MB)", filename, size_mb)

    from app.intelligence.document_intelligence import analyze_document_json

    file_type = ext.lstrip(".")
    mime = None
    if file.content_type and file.content_type != "application/octet-stream":
        mime = file.content_type

    report = await run_in_threadpool(
        analyze_document_json,
        file_bytes,
        file_type=file_type,
        mime_type=mime,
        filename=filename,
    )

    return {
        "success": True,
        "filename": filename,
        **report,
    }


@app.post("/api/extract")
async def extract_api(
    file: UploadFile = File(...),
    lang: str = Form("auto"),
    mode: str = Form("auto"),
    fidelity: str = Form("forensic"),
):
    """
    Extract text from document.

    Modes:
        - "direct": Text layer extraction only (fastest, 95-100% accuracy for digital PDFs)
        - "ocr": Image OCR only (for scanned documents)
        - "auto": Try direct first, OCR fallback for pages without text

    Fidelity:
        - "forensic": as-is (default) — no dictionary/cross-page mutations
        - "balanced": light cleanup only
        - "assisted": optional knowledge-base repairs
        - "ocr_max": scan OCR path
    """
    from app.extract.fidelity import normalize_fidelity

    if lang not in ("auto", "eng", "nep", "eng+nep"):
        raise ValueError(f"Invalid language: {lang}")

    if mode not in ("auto", "direct", "ocr"):
        raise ValueError(f"Invalid mode: {mode}. Use 'auto', 'direct', or 'ocr'.")

    try:
        fidelity_mode = normalize_fidelity(fidelity)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc

    filename = file.filename or "unknown"
    ext = os.path.splitext(filename)[1].lower()

    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(f"File type {ext} not allowed. Supported: {', '.join(ALLOWED_EXTENSIONS)}")

    file_bytes = await file.read()
    size_mb = len(file_bytes) / (1024 * 1024)

    if size_mb > MAX_FILE_SIZE_MB:
        raise ValueError(f"File ({size_mb:.1f}MB) exceeds {MAX_FILE_SIZE_MB}MB limit.")

    logger.info(
        f"Processing: {filename} ({size_mb:.2f}MB), lang={lang}, mode={mode}, fidelity={fidelity_mode}"
    )

    from app.extract.fidelity import reset_fidelity, set_fidelity
    fidelity_token = set_fidelity(fidelity_mode)

    try:
        if ext == ".pdf":
            result = await run_in_threadpool(
                extract_pdf, file_bytes, lang, mode, fidelity_mode
            )
        elif ext == ".docx":
            result = await run_in_threadpool(extract_docx, file_bytes, lang, mode)
        else:
            # Images always use OCR
            result = await run_in_threadpool(extract_image, file_bytes, lang)
    except MemoryError:
        return _memory_error_response(
            f"OCR for {filename}",
            "Server ran out of memory while processing this file. "
            "Try a smaller PDF, fewer pages, or lower-resolution scan.",
        )
    finally:
        reset_fidelity(fidelity_token)

    text = result.pop("text", "")

    return {
        "success": True,
        "text": text,
        "filename": filename,
        "lang": lang,
        "mode": mode,
        "fidelity": fidelity_mode,
        "meta": result,
    }


@app.post("/api/extract-txt")
async def extract_txt_api(
    file: UploadFile = File(...),
    lang: str = Form("auto"),
    mode: str = Form("auto"),
    fidelity: str = Form("forensic"),
    page_separators: bool = Form(True),
    headers_footers: bool = Form(True),
    quality_report: bool = Form(False),
    bom: bool = Form(False),
):
    """
    Extract text from a document and return a clean .txt file download.

    Default fidelity is forensic (as-is): no unsupervised word mutations.
    """
    from app.extract.fidelity import normalize_fidelity

    filename = file.filename or "document"
    ext = os.path.splitext(filename)[1].lower()

    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(f"File type {ext} not allowed.")

    if mode not in ("auto", "direct", "ocr"):
        raise ValueError(f"Invalid mode: {mode}.")

    try:
        fidelity_mode = normalize_fidelity(fidelity)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc

    file_bytes = await file.read()
    size_mb = len(file_bytes) / (1024 * 1024)

    if size_mb > MAX_FILE_SIZE_MB:
        raise ValueError(f"File ({size_mb:.1f}MB) exceeds {MAX_FILE_SIZE_MB}MB limit.")

    logger.info(
        "extract-txt: %s (%.2f MB), lang=%s, mode=%s, fidelity=%s",
        filename, size_mb, lang, mode, fidelity_mode,
    )

    from app.extract.fidelity import reset_fidelity, set_fidelity
    fidelity_token = set_fidelity(fidelity_mode)

    try:
        if ext == ".pdf":
            result = await run_in_threadpool(
                extract_pdf, file_bytes, lang, mode, fidelity_mode
            )
        elif ext == ".docx":
            result = await run_in_threadpool(extract_docx, file_bytes, lang, mode)
        else:
            result = await run_in_threadpool(extract_image, file_bytes, lang)
    finally:
        reset_fidelity(fidelity_token)

    result.setdefault("fidelity", fidelity_mode)

    txt_content = format_as_txt(
        result,
        include_page_separators=page_separators,
        include_headers_footers=headers_footers,
        include_quality_report=quality_report,
        utf8_bom=bom,
    )

    # Build download filename, preserving non-ASCII (e.g. Nepali) names
    # via the shared RFC 5987-aware helper instead of ASCII-slugifying them
    # away.
    base = os.path.splitext(filename)[0]
    download_name = f"{base}_extracted.txt"

    return Response(
        content=txt_content.encode("utf-8"),
        media_type="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": attachment_disposition(download_name),
            "X-Fidelity": fidelity_mode,
            "X-Extract-Mode": mode,
            "X-Mean-Confidence": str(result.get("mean_confidence", "")),
            "X-Method": str(result.get("method", "")),
        },
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
        return _memory_error_response(
            f"Conversion for {filename}",
            "Server ran out of memory. Try a smaller file or lower DPI.",
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
    
    logger.info(f"Classified intent: '{intent}' for {len(text)} chars of input text")
    
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
            content={"success": False, "detail": "Font detection failed"},
        )

    return {"success": True, "filename": filename, **result}


@app.post("/api/extract/smart-txt")
async def extract_pdf_smart_txt_api(
    file: UploadFile = File(...),
):
    """
    High-accuracy font-aware PDF → TXT (no OCR).

    Reads the PDF text layer directly and converts legacy Nepali fonts
    (Preeti, Kantipur, Sagarmatha, etc.) to proper Unicode. This is more
    accurate than OCR for PDFs that already contain embedded text.
    """
    filename = file.filename or "unknown.pdf"
    ext = os.path.splitext(filename)[1].lower()

    if ext != ".pdf":
        raise ValueError("Only PDF files are supported for this endpoint.")

    file_bytes = await file.read()
    size_mb = len(file_bytes) / (1024 * 1024)
    if size_mb > MAX_FILE_SIZE_MB:
        raise ValueError(f"File ({size_mb:.1f}MB) exceeds {MAX_FILE_SIZE_MB}MB limit.")

    logger.info(f"Smart-TXT pipeline: {filename} ({size_mb:.2f}MB)")

    try:
        from app.nlp.ai_corrector import process_pdf_smart
        result = await run_in_threadpool(process_pdf_smart, file_bytes)
    except MemoryError:
        return _memory_error_response(
            f"Smart-TXT pipeline for {filename}",
            "Out of memory. Try a smaller PDF.",
        )
    except Exception as e:
        logger.error(f"Smart-TXT pipeline error: {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"success": False, "detail": "Pipeline failed"},
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
        "X-Quality-Score": str(result.get("quality", {}).get("score", 0)),
        "X-Method": result.get("method", "direct_font_conversion"),
        "X-Tables-Detected": str(result.get("tables_detected", 0)),
        "X-Tables-Borderless": str(result.get("tables_by_method", {}).get("column_clustering", 0)),
    }

    return Response(
        content=result["text"].encode("utf-8"),
        media_type="text/plain; charset=utf-8",
        headers=headers,
    )


# Mount frontend last
frontend_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend")
if os.path.exists(frontend_dir):
    app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")
