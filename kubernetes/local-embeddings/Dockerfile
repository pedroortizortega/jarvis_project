FROM python:3.12-slim AS base

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# D-02: bake the pinned ONNX model into the image at build time so the
# running pod never needs network access to fetch it. Written as root,
# owned by root (mode 0755 by default) — readable by the runtime uid
# 10001 below, writable by nobody. Lives outside /app so a later
# `COPY app ./app` layer rebuild does not invalidate this ~2.24GB layer
# (multilingual-e5-large; corrected 2026-08-22, was wrongly ~500MB when
# the pin was still the nonexistent -small variant).
ENV HF_HUB_OFFLINE=0 \
    TRANSFORMERS_OFFLINE=0
RUN python -c "\
from fastembed import TextEmbedding; \
TextEmbedding('intfloat/multilingual-e5-large', cache_dir='/opt/models/fastembed')"

RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin local-embeddings
USER 10001

EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
