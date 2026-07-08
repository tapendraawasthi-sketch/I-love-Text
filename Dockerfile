FROM python:3.11-slim

# Install system dependencies including Ollama
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    poppler-utils \
    libgl1 \
    libglib2.0-0 \
    wget \
    ca-certificates \
    curl \
    && mkdir -p /usr/share/tesseract-ocr/5/tessdata \
    && wget -q https://github.com/tesseract-ocr/tessdata_best/raw/main/eng.traineddata \
         -O /usr/share/tesseract-ocr/5/tessdata/eng.traineddata \
    && wget -q https://github.com/tesseract-ocr/tessdata_best/raw/main/nep.traineddata \
         -O /usr/share/tesseract-ocr/5/tessdata/nep.traineddata \
    && rm -rf /var/lib/apt/lists/*

# Install Ollama for LLM-based text correction
RUN curl -fsSL https://ollama.ai/install.sh | sh || true

ENV TESSDATA_PREFIX=/usr/share/tesseract-ocr/5/tessdata
ENV RENDER=true
ENV OCR_PAGE_WORKERS=1
ENV PDF_RENDER_DPI=300
ENV MAX_OCR_DIMENSION=3200
# LLM Configuration
ENV ENABLE_LLM_OCR_ENHANCEMENT=true
ENV OLLAMA_BASE_URL=http://localhost:11434
ENV FAST_MODEL=mistral
ENV OCR_LLM_CONFIDENCE_THRESHOLD=0.75

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

# Start both Ollama and FastAPI
CMD ["sh", "-c", "(ollama serve &) && sleep 5 && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
