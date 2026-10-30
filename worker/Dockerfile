# Local worker (see local.py). Mirrors the Modal images in app.py, with CPU separation.
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg curl unzip ca-certificates build-essential \
    && rm -rf /var/lib/apt/lists/*
# build-essential: diffq (audio-separator dep) has no arm64 wheel.
# yt-dlp needs a JS runtime (deno) for YouTube.
RUN curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh -s -- -y

# CPU-only torch: skips the CUDA wheels.
RUN pip install --no-cache-dir --extra-index-url https://download.pytorch.org/whl/cpu \
    "audio-separator[cpu]==0.47.0" openai "fastapi[standard]"

ENV MODEL_DIR=/models DATA_DIR=/data
# Bake model weights into the image so the first song skips the download.
RUN python -c "from audio_separator.separator import Separator; \
Separator(model_file_dir='/models').download_model_files('vocals_mel_band_roformer.ckpt')"

# Last, so its own layer can be refreshed on its own: docker compose build --build-arg YT_DLP_CACHE_BUST=$(date +%F)
ARG YT_DLP_CACHE_BUST
RUN pip install --no-cache-dir -U --pre "yt-dlp[default]"

WORKDIR /app
COPY pipeline.py lyrics.py local.py ./
EXPOSE 8000
CMD ["uvicorn", "local:app", "--host", "0.0.0.0", "--port", "8000"]
