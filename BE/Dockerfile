# ── Stage 1: Build ──────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build

COPY requirements.txt .

# Install CPU-only PyTorch FIRST. Docling pulls torch transitively; if pip resolves
# it from PyPI it grabs the ~2GB CUDA build, but Cloud Run has no GPU. The CPU wheel
# is a fraction of the size — this cuts minutes off the build and hundreds of MB off
# the image. Must precede the requirements install so torch is already satisfied when
# Docling's dependencies resolve.
RUN pip install --no-cache-dir --prefix=/install \
        --index-url https://download.pytorch.org/whl/cpu torch

RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# python-jobspy hard-pins numpy==1.26.3, which conflicts with the numpy>=2.0 that
# Docling requires (pip would fail with ResolutionImpossible). jobspy runs fine on
# numpy 2.x, so install it WITHOUT deps — its real runtime deps are already in
# requirements.txt above.
RUN pip install --no-cache-dir --prefix=/install --no-deps "python-jobspy>=1.1.75"

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
