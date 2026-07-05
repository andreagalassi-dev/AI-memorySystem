FROM python:3.11-slim

WORKDIR /app

# Dipendenze base OS
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    poppler-utils \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# Install dependencies (uvicorn needed for health endpoint)
RUN pip install --no-cache-dir -r requirements.txt uvicorn fastapi

ENV PYTHONPATH=/app
ENV WATCHDOG_CORPUS_PATH=/corpus

# Avvio del demone watchdog
CMD ["python", "-m", "src.watchdog.daemon"]
