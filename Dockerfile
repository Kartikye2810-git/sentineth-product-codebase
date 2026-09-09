FROM python:3.13-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 \
    SENTINETH_STORAGE_DIR=/data/documents HF_HOME=/data/models
WORKDIR /app/backend
COPY backend/requirements.txt ./requirements.txt
# The default Nemotron deployment uses no local model weights. Keep the same
# dependency pins while making the offline model an explicit build option.
ARG WITH_LOCAL_MODELS=false
RUN if [ "$WITH_LOCAL_MODELS" = true ]; then \
      pip install torch==2.13.0 --index-url https://download.pytorch.org/whl/cpu && pip install -r requirements.txt; \
    else \
      sed '/^sentence-transformers==/d; /^transformers==/d; /^torch==/d' requirements.txt > requirements-hosted.txt && pip install -r requirements-hosted.txt; \
    fi
RUN groupadd --gid 10001 sentineth && useradd --uid 10001 --gid sentineth --create-home sentineth \
    && mkdir -p /data/documents /data/models && chown -R sentineth:sentineth /data
COPY --chown=sentineth:sentineth backend/ ./
USER sentineth
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/ready', timeout=8)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers", "--no-access-log"]
