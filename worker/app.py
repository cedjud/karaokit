"""Song pipeline on Modal: download (yt-dlp) -> separate (audio-separator, GPU) -> transcribe (Whisper API, vocal stem).

Outputs land in R2 with the same layout as ./public:
  audio/<name>.mp3, stems/<name>/{vocals,no_vocals}.mp3, transcripts/<name>.json,
  lyrics/<name>.json (verified against LRCLIB), meta/<name>.json ({artist, title, language, lyrics})
Plus source/<name>.<ext>: the original download, never re-encoded, fed to separation.

language is a Whisper ISO-639-1 code ("en", "fr", ...) or "auto" to let Whisper detect it.

CLI:    modal run worker/app.py --url <url> [--name artist_title] [--language fr]
Redo a transcript: modal run worker/app.py::transcribe --name <id> --language fr
Verify lyrics:     modal run worker/app.py::verify_lyrics --name <id>
                   (LRCLIB + LLM -> lyrics/<id>.json; see lyrics.py)
Redo stems:        modal run worker/app.py::separate --name <id> --force
Try another model: modal run worker/app.py::separate --name <id> --model htdemucs_ft.yaml
                   (writes to compare/<model>/<id>, leaving stems/ untouched)
Deploy: modal deploy worker/app.py
Local (no Modal): see local.py; the steps themselves live in pipeline.py.
"""

import os
import shlex
from datetime import date

import modal

import pipeline
from pipeline import MODEL_DIR, SEPARATION_MODEL, YT_DLP_INSTALL

R2_ACCOUNT_ID = "e9a4adf8f5a2233cc086a8c96dc7e03a"
R2_BUCKET = "music-wheel"
# pipeline.DATA_DIR defaults to this in the containers.
MOUNT = "/data"

app = modal.App("music-wheel")

# Needs AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY from an R2 API token.
r2 = modal.CloudBucketMount(
    bucket_name=R2_BUCKET,
    bucket_endpoint_url=f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
    secret=modal.Secret.from_name("r2-credentials"),
)
volumes = {MOUNT: r2}

# yt-dlp needs a JS runtime (deno) for YouTube.
# The date in the command busts Modal's layer cache on the next day's deploy.
download_image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("ffmpeg", "curl", "unzip")
    .run_commands("curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh -s -- -y")
    .run_commands(f"{shlex.join(YT_DLP_INSTALL)}  # {date.today()}")
    .add_local_python_source("pipeline")
)

separate_image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("ffmpeg")
    .pip_install("audio-separator[gpu]==0.47.0")
    # Bake model weights into the image so cold starts skip the download.
    .run_commands(
        "python -c \"from audio_separator.separator import Separator; "
        f"Separator(model_file_dir='{MODEL_DIR}').download_model_files('{SEPARATION_MODEL}')\""
    )
    .add_local_python_source("pipeline")
)

transcribe_image = (
    modal.Image.debian_slim(python_version="3.12").pip_install("openai").add_local_python_source("pipeline", "lyrics")
)

web_image = (
    modal.Image.debian_slim(python_version="3.12").pip_install("fastapi[standard]").add_local_python_source("pipeline")
)

# The orchestrator only calls other functions.
base_image = modal.Image.debian_slim(python_version="3.12").add_local_python_source("pipeline")


# --- steps (see pipeline.py) ---


@app.function(image=download_image, volumes=volumes, timeout=600, retries=1)
def download(url: str, name: str | None = None) -> str:
    return pipeline.download(url, name)


@app.function(image=separate_image, volumes=volumes, gpu="T4", timeout=900)
def separate(name: str, model: str = SEPARATION_MODEL, force: bool = False) -> str:
    return pipeline.separate(name, model, force)


@app.function(
    image=transcribe_image,
    volumes=volumes,
    secrets=[modal.Secret.from_name("openai")],
    timeout=300,
)
def transcribe(name: str, language: str = "en", force: bool = False) -> str:
    return pipeline.transcribe(name, language, force)


@app.function(
    image=transcribe_image,
    volumes=volumes,
    secrets=[modal.Secret.from_name("openai")],
    timeout=600,
)
def verify_lyrics(name: str) -> str | None:
    return pipeline.verify_lyrics(name)


@app.function(image=base_image, timeout=1800)
def add_song(url: str, name: str | None = None, language: str = "en") -> dict:
    name = download.remote(url, name)
    # Sequential: transcription reads the vocal stem.
    stems = separate.remote(name)
    transcript = transcribe.remote(name, language)
    # Optional: songs without reference lyrics fall back to the raw transcript.
    try:
        verified = verify_lyrics.remote(name)
    except Exception as e:
        print(f"Lyrics verification failed: {e}")
        verified = None
    return {"name": name, "stems": stems, "transcript": transcript, "lyrics": verified}


# --- HTTP API for the web UI ---


@app.function(image=web_image, volumes=volumes, secrets=[modal.Secret.from_name("music-wheel-api")])
@modal.asgi_app()
def api():
    from fastapi import Depends, FastAPI, Header, HTTPException
    from fastapi.middleware.cors import CORSMiddleware
    from pydantic import BaseModel

    web = FastAPI()
    web.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

    def auth(authorization: str = Header("")):
        if authorization != f"Bearer {os.environ['API_TOKEN']}":
            raise HTTPException(401)

    class SongRequest(BaseModel):
        url: str
        name: str | None = None
        language: str = "en"

    # Public: only lists songs that are fully processed.
    @web.get("/songs")
    def songs():
        return pipeline.list_songs()

    @web.post("/songs", dependencies=[Depends(auth)])
    def create(req: SongRequest):
        return {"call_id": add_song.spawn(req.url, req.name, req.language).object_id}

    @web.get("/jobs/{call_id}", dependencies=[Depends(auth)])
    def status(call_id: str):
        try:
            return {"status": "done", "result": modal.FunctionCall.from_id(call_id).get(timeout=0)}
        except TimeoutError:
            return {"status": "pending"}
        except Exception as e:
            return {"status": "failed", "error": str(e)}

    return web


# --- CLI ---


@app.local_entrypoint()
def main(url: str, name: str = "", language: str = "en"):
    print(add_song.remote(url, name or None, language))
