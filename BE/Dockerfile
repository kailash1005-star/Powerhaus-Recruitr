# ── Stage 1: Build ──────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build

COPY requirements.txt .

RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# python-jobspy hard-pins numpy==1.26.3, which conflicts with the numpy>=2.0 the
# rest of the stack uses (pip would fail with ResolutionImpossible). jobspy runs
# fine on numpy 2.x, so install it WITHOUT deps — its real runtime deps are
# already in requirements.txt above.
RUN pip install --no-cache-dir --prefix=/install --no-deps "python-jobspy>=1.1.75"

# ── Stage 2: Runtime ────────────────────────────────────────────
FROM python:3.12-slim

# Prevent Python from writing .pyc files and enable unbuffered logs
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application source
COPY . .

# Cloud Run injects PORT env var (default 8080)
ENV PORT=8080
EXPOSE ${PORT}

# Run with uvicorn — workers configurable via UVICORN_WORKERS env var
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers ${UVICORN_WORKERS:-1}"]
