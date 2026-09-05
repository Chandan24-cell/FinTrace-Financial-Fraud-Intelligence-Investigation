# Sentinel-G FastAPI — production image for Oracle Cloud VM
# Python 3.11 base; uv for fast dependency installs.
#
# Note: pyproject.toml sets `[tool.uv] package = false`, so we cannot
# `uv pip install .` — instead we install dependencies declared in
# `[project.dependencies]` via `-r pyproject.toml`, then COPY source.
# libgomp1 is required by lightgbm at runtime.

FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    libgomp1 \
    poppler-utils \
    tesseract-ocr \
    ghostscript \
    libgl1 \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --upgrade pip uv

WORKDIR /app

COPY pyproject.toml README.md ./
RUN uv pip install --system -r pyproject.toml

COPY backend ./backend
COPY ml ./ml
# Demo-critical assets — required at runtime for analyse / search / seeds.
COPY scripts ./scripts
COPY infra/seeds ./infra/seeds
COPY data/labels ./data/labels

EXPOSE 8000

CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
