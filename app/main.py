"""
Main FastAPI application.
"""
import os
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

logger = get_logger("TextExtract")

app = FastAPI(title="TextExtract", version="2.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
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
    try:
        from npttf2utf import npttf2utf
        logger.info("npttf2utf loaded successfully - legacy font conversion available")
    except ImportError:
        logger.warning("npttf2utf NOT installed. Legacy font conversion will be limited.")


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"success": False, "detail": "Internal server error", "error": str(exc)}
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
            "Content-Disposition": f'attachment; filename="{output_filename}"',
            "X-Pages": str(meta["pages"]),
            "X-DPI": str(meta["dpi"]),
            "X-Output-Size-MB": str(meta["output_size_mb"]),
        },
    )


# Mount frontend last
frontend_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend")
if os.path.exists(frontend_dir):
    app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")
