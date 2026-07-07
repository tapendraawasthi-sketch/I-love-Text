FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    poppler-utils \
    libgl1 \
    libglib2.0-0 \
    wget \
    ca-certificates \
    && mkdir -p /usr/share/tesseract-ocr/5/tessdata \
    && wget -q https://github.com/tesseract-ocr/tessdata_best/raw/main/eng.traineddata \
         -O /usr/share/tesseract-ocr/5/tessdata/eng.traineddata \
    && wget -q https://github.com/tesseract-ocr/tessdata_best/raw/main/nep.traineddata \
         -O /usr/share/tesseract-ocr/5/tessdata/nep.traineddata \
    && rm -rf /var/lib/apt/lists/*

ENV TESSDATA_PREFIX=/usr/share/tesseract-ocr/5/tessdata

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
