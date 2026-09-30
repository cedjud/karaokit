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
"""

import glob
import json
import os
import re
import shutil
import subprocess
import tempfile

import modal

R2_ACCOUNT_ID = "e9a4adf8f5a2233cc086a8c96dc7e03a"
R2_BUCKET = "music-wheel"
MOUNT = "/data"
# Kim's Mel-Band RoFormer: best vocals score in audio-separator's benchmarks.
SEPARATION_MODEL = "vocals_mel_band_roformer.ckpt"
MODEL_DIR = "/models"

app = modal.App("music-wheel")

# Needs AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY from an R2 API token.
r2 = modal.CloudBucketMount(
    bucket_name=R2_BUCKET,
    bucket_endpoint_url=f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
    secret=modal.Secret.from_name("r2-credentials"),
)
volumes = {MOUNT: r2}

# yt-dlp needs a JS runtime (deno) for YouTube.
download_image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("ffmpeg", "curl", "unzip")
    .run_commands("curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh -s -- -y")
    .pip_install("yt-dlp[default]")
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
)

transcribe_image = modal.Image.debian_slim(python_version="3.12").pip_install("openai")

lyrics_image = transcribe_image.add_local_python_source("lyrics")

web_image = modal.Image.debian_slim(python_version="3.12").pip_install("fastapi[standard]")


# --- naming (ported from download.ts) ---

NOISE_TAG = re.compile(
    r"\s*[([][^)\]]*\b(official|video|audio|lyrics?|visuali[sz]er|hd|hq|4k|remaster(ed)?|mv)\b[^)\]]*[)\]]",
    re.IGNORECASE,
)


def kebab(part: str) -> str:
    part = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", part)
    return "-".join(w.lower() for w in re.findall(r"[^\W_]+", part))


# Kebab-case each part, keeping "_" as the artist/title separator.
def to_file_name(name: str) -> str:
    parts = (kebab(re.sub(r"['’]", "", p)) for p in name.split("_"))
    return "_".join(p for p in parts if p)


# "Artist - Title (Official Video)" -> "Artist_Title"
def clean_title(title: str) -> str:
    return re.sub(r"\s+[-–—|]\s+", "_", NOISE_TAG.sub("", title), count=1).strip()


# "hotel-california" -> "Hotel California"; mixed case is kept as is.
def display(part: str) -> str:
    if part != part.lower():
        return part.strip()
    return " ".join(w[:1].upper() + w[1:] for w in part.replace("-", " ").split())


# "Artist_Title" -> {"artist", "title"}; no "_" means title only.
def to_meta(raw: str) -> dict:
    artist, _, title = raw.partition("_")
    if not title:
        artist, title = "", artist
    return {"artist": display(artist), "title": display(title)}


def read_meta(name: str) -> dict:
    try:
        with open(f"{MOUNT}/meta/{name}.json") as f:
            return json.load(f)
    except FileNotFoundError:
        return to_meta(name)


def update_meta(name: str, **fields) -> None:
    path = f"{MOUNT}/meta/{name}.json"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    meta = read_meta(name) | fields
    with open(path, "w") as f:
        json.dump(meta, f, ensure_ascii=False)


# --- steps ---


@app.function(image=download_image, volumes=volumes, timeout=600, retries=1)
def download(url: str, name: str | None = None) -> str:
    def yt_dlp(*args: str) -> str:
        result = subprocess.run(["yt-dlp", "--no-playlist", *args], capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError(f"yt-dlp failed: {result.stderr.strip()}")
        return result.stdout.strip()

    raw = name or clean_title(yt_dlp("--print", "title", url))
    name = to_file_name(raw)
    if not name:
        raise ValueError("Could not derive a file name")

    if not os.path.exists(f"{MOUNT}/meta/{name}.json"):
        update_meta(name, **to_meta(raw))

    dest = f"{MOUNT}/audio/{name}.mp3"
    if os.path.exists(dest) and find_source(name):
        print(f"{name} already downloaded, skipping")
        return name

    # Bucket mounts only support sequential writes, so work in /tmp and copy.
    with tempfile.TemporaryDirectory() as tmp:
        # Keep the original codec (usually opus): separation gets the audio without an extra lossy pass.
        src = yt_dlp(
            "-f", "bestaudio", "-x", "--no-simulate", "--print", "after_move:filepath",
            "-o", f"{tmp}/{name}.%(ext)s", url,
        ).splitlines()[-1]
        subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-i", src, "-q:a", "0", f"{tmp}/{name}.mp3"], check=True
        )
        source_dest = f"{MOUNT}/source/{os.path.basename(src)}"
        copies = [(src, source_dest)]
        # An existing mp3 is kept: its transcript's timestamps were made from it.
        if not os.path.exists(dest):
            copies.append((f"{tmp}/{name}.mp3", dest))
        for path, target in copies:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copyfile(path, target)
    print("Saved " + ", ".join(target for _, target in copies))
    return name


# Songs downloaded before source/ existed only have the mp3.
def find_source(name: str) -> str | None:
    matches = glob.glob(f"{MOUNT}/source/{glob.escape(name)}.*")
    return matches[0] if matches else None


@app.function(image=separate_image, volumes=volumes, gpu="T4", timeout=900)
def separate(name: str, model: str = SEPARATION_MODEL, force: bool = False) -> str:
    from audio_separator.separator import Separator

    out_dir = f"{MOUNT}/stems/{name}" if model == SEPARATION_MODEL else f"{MOUNT}/compare/{model}/{name}"
    if os.path.exists(f"{out_dir}/vocals.mp3") and not force:
        print(f"{name} already separated, skipping")
        return out_dir

    source = find_source(name) or f"{MOUNT}/audio/{name}.mp3"
    with tempfile.TemporaryDirectory() as tmp:
        src = f"{tmp}/{os.path.basename(source)}"
        shutil.copyfile(source, src)
        separator = Separator(
            model_file_dir=MODEL_DIR, output_dir=f"{tmp}/out", output_format="MP3", output_bitrate="320k"
        )
        separator.load_model(model)
        # Vocal models call the rest "other" or "instrumental". Demucs (.yaml) has 4 stems, so its "other"
        # isn't the full backing track: keep its own names there.
        names = {"vocals": "vocals", "instrumental": "no_vocals"}
        if not model.endswith(".yaml"):
            names["other"] = "no_vocals"
        separator.separate(src, names)
        os.makedirs(out_dir, exist_ok=True)
        for stem in os.listdir(f"{tmp}/out"):
            shutil.copyfile(f"{tmp}/out/{stem}", f"{out_dir}/{stem}")
    print(f"Saved {out_dir} (from {os.path.basename(source)}, {model})")
    return out_dir


@app.function(
    image=transcribe_image,
    volumes=volumes,
    secrets=[modal.Secret.from_name("openai")],
    timeout=300,
)
def transcribe(name: str, language: str = "en") -> str:
    from openai import OpenAI

    options = {}
    if language != "auto":
        options["language"] = language
    if language == "en":
        # Whisper treats the prompt as preceding transcript, not instructions,
        # so it's written as sample lyrics to set the style (vocalizations, repeats).
        # English-only: it would pull other languages towards English.
        options["prompt"] = "Oh, oh, oh. La, la, la. Yeah, yeah. Na na na, na na na."

    dest = f"{MOUNT}/transcripts/{name}.json"
    # The vocal stem: no backing track to mishear, and timestamps match the stems the app plays.
    audio = f"{MOUNT}/stems/{name}/vocals.mp3"
    if not os.path.exists(audio):
        audio = f"{MOUNT}/audio/{name}.mp3"
    with open(audio, "rb") as f:
        transcription = OpenAI().audio.transcriptions.create(
            file=f,
            model="whisper-1",
            response_format="verbose_json",
            timestamp_granularities=["word", "segment"],
            **options,
        )
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "w") as f:
        json.dump(transcription.model_dump(), f, indent=2)
    # Whisper reports the language it used, e.g. "french" (also when auto-detected).
    update_meta(name, language=transcription.language)
    print(f"Saved {dest} ({transcription.language}, from {os.path.relpath(audio, MOUNT)})")
    return dest


@app.function(
    image=lyrics_image,
    volumes=volumes,
    secrets=[modal.Secret.from_name("openai")],
    timeout=600,
)
def verify_lyrics(name: str) -> str | None:
    import lyrics

    with open(f"{MOUNT}/transcripts/{name}.json") as f:
        transcript = json.load(f)
    result = lyrics.verify(transcript, read_meta(name))
    if not result or not result["segments"]:
        update_meta(name, lyrics="none")
        print(f"No reference lyrics for {name}")
        return None
    dest = f"{MOUNT}/lyrics/{name}.json"
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "w") as f:
        json.dump(result, f, ensure_ascii=False)
    update_meta(name, lyrics="synced" if result["synced"] else "plain")
    print(f"Saved {dest} (lrclib {result['lrclibId']}, synced={result['synced']}, offset={result['offset']})")
    return dest


@app.function(timeout=1800)
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
        stems = f"{MOUNT}/stems"
        ready = [
            name
            for name in sorted(os.listdir(stems) if os.path.isdir(stems) else [])
            if all(
                os.path.exists(p)
                for p in [
                    f"{stems}/{name}/vocals.mp3",
                    f"{stems}/{name}/no_vocals.mp3",
                    f"{MOUNT}/transcripts/{name}.json",
                ]
            )
        ]
        return [{"id": name, **read_meta(name)} for name in ready]

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
