FROM python:3.11-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

# faster-whisper >= 1.1 is required for the large-v3-turbo model alias.
RUN pip install --no-cache-dir "faster-whisper>=1.1.0"

# Keep CTranslate2 from oversubscribing threads; the script sets cpu_threads itself.
ENV OMP_NUM_THREADS=1 \
    PYTHONUNBUFFERED=1 \
    HF_HUB_DISABLE_TELEMETRY=1

WORKDIR /app
COPY stash_subs.py /app/stash_subs.py

ENTRYPOINT ["python", "/app/stash_subs.py"]
