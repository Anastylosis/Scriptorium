# syntax=docker/dockerfile:1.7
ARG PYTHON_VERSION=3.12

FROM python:${PYTHON_VERSION}-slim

LABEL org.opencontainers.image.title="stash-subs" \
      org.opencontainers.image.description="Tag-driven subtitle generation for Stash" \
      org.opencontainers.image.source="https://github.com/Anastylosis/stash-subs" \
      org.opencontainers.image.licenses="GPL-3.0-only"

# No ffmpeg package: PyAV is already a dependency of faster-whisper and its
# wheel carries the FFmpeg libraries, so decoding needs no separate binary.
COPY requirements.txt /tmp/requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r /tmp/requirements.txt && rm /tmp/requirements.txt

# OMP_NUM_THREADS: CTranslate2 oversubscribes otherwise; the worker sets cpu_threads itself.
# HF_HOME lives under /models so tokenizer downloads survive container recreation.
ENV PYTHONUNBUFFERED=1 \
    OMP_NUM_THREADS=1 \
    HF_HUB_DISABLE_TELEMETRY=1 \
    HF_HOME=/models/hf \
    MODEL_DIR=/models \
    HTTP_PORT=8088

WORKDIR /app
COPY stash_subs/ /app/stash_subs/

VOLUME ["/models"]
EXPOSE 8088

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('HTTP_PORT','8088')+'/json',timeout=5)"]

ENTRYPOINT ["python", "-m", "stash_subs"]
