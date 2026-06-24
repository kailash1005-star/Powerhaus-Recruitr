# ── Stage 1: Build ──────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── Stage 2: Runtime ────────────────────────────────────────────
FROM python:3.12-slim

# Prevent Python from writing .pyc files and enable unbuffered logs
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# System libraries the document parser (Docling / OpenCV) needs at runtime.
# Without these the AI parser fails to import in the cloud.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Pre-download the Docling AI models INTO the image so the first CV upload in
# production is instant (instead of downloading hundreds of MB on first request).
# This is the slow part of the build — it only runs once, at build time.
RUN python -c "from docling.utils.model_downloader import download_models; download_models()"

# Copy application source
COPY . .

# Cloud Run injects PORT env var (default 8080)
ENV PORT=8080
EXPOSE ${PORT}

# Run with uvicorn — workers configurable via UVICORN_WORKERS env var
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers ${UVICORN_WORKERS:-1}"]
