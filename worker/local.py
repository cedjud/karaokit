"""Local version of the Modal worker: the same HTTP API, running the pipeline in-process.

Files go to DATA_DIR (default ./data) and are served under /media, so the web UI works with
  VITE_API_URL=http://localhost:8000  VITE_MEDIA_URL=http://localhost:8000/media
(npm run dev:local). Separation runs on CPU (or Apple MPS when run natively), so it's slower than on Modal.

Docker: docker compose up --build
Native: DATA_DIR=data MODEL_DIR=.models uv run --python 3.11 --env-file .env \
          --with 'audio-separator[cpu]==0.47.0' --with openai --with 'fastapi[standard]' --with 'yt-dlp[default]' \
          fastapi dev worker/local.py
        (needs ffmpeg and deno on PATH)
CLI:    python worker/local.py <url> [name] [language]
"""

import os
import sys
import traceback
import uuid
from concurrent.futures import Future, ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("DATA_DIR", os.path.abspath("data"))

import pipeline  # noqa: E402


def add_song(url: str, name: str | None = None, language: str = "en") -> dict:
    name = pipeline.download(url, name)
    # Sequential: transcription reads the vocal stem.
    stems = pipeline.separate(name)
    transcript = pipeline.transcribe(name, language)
    # Optional: songs without reference lyrics fall back to the raw transcript.
    try:
        verified = pipeline.verify_lyrics(name)
    except Exception as e:
        print(f"Lyrics verification failed: {e}")
        verified = None
    return {"name": name, "stems": stems, "transcript": transcript, "lyrics": verified}


def create_app():
    from fastapi import Depends, FastAPI, Header, HTTPException
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.staticfiles import StaticFiles
    from pydantic import BaseModel

    token = os.environ.get("API_TOKEN", "local")
    # One song at a time: separation uses every core.
    executor = ThreadPoolExecutor(max_workers=1)
    # In memory: jobs are forgotten on restart (their files stay).
    jobs: dict[str, Future] = {}

    web = FastAPI()
    web.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
    os.makedirs(pipeline.DATA_DIR, exist_ok=True)
    web.mount("/media", StaticFiles(directory=pipeline.DATA_DIR), name="media")

    def auth(authorization: str = Header("")):
        if authorization != f"Bearer {token}":
            raise HTTPException(401)

    class SongRequest(BaseModel):
        url: str
        name: str | None = None
        language: str = "en"

    @web.get("/songs")
    def songs():
        return pipeline.list_songs()

    def run(req: SongRequest) -> dict:
        try:
            return add_song(req.url, req.name, req.language)
        except Exception:
            traceback.print_exc()
            raise

    @web.post("/songs", dependencies=[Depends(auth)])
    def create(req: SongRequest):
        call_id = uuid.uuid4().hex
        jobs[call_id] = executor.submit(run, req)
        return {"call_id": call_id}

    @web.get("/jobs/{call_id}", dependencies=[Depends(auth)])
    def status(call_id: str):
        job = jobs.get(call_id)
        if not job:
            raise HTTPException(404)
        if not job.done():
            return {"status": "pending"}
        if job.exception():
            return {"status": "failed", "error": str(job.exception())}
        return {"status": "done", "result": job.result()}

    return web


if __name__ == "__main__":
    print(add_song(*sys.argv[1:4]))
else:
    app = create_app()
