"""Song pipeline steps, independent of where they run (Modal: app.py, local/Docker: local.py).

All files live under DATA_DIR (an R2 mount on Modal, a plain directory locally), with the layout
described in app.py. Heavy dependencies are imported inside the steps that need them.
"""

import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

DATA_DIR = os.environ.get("DATA_DIR", "/data")
# Kim's Mel-Band RoFormer: best vocals score in audio-separator's benchmarks.
SEPARATION_MODEL = "vocals_mel_band_roformer.ckpt"
MODEL_DIR = os.environ.get("MODEL_DIR", "/models")
# Nightly (--pre) ships YouTube fixes first.
YT_DLP_INSTALL = ["pip", "install", "-U", "--pre", "yt-dlp[default]"]


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
        with open(f"{DATA_DIR}/meta/{name}.json") as f:
            return json.load(f)
    except FileNotFoundError:
        return to_meta(name)


def update_meta(name: str, **fields) -> None:
    path = f"{DATA_DIR}/meta/{name}.json"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    meta = read_meta(name) | fields
    with open(path, "w") as f:
        json.dump(meta, f, ensure_ascii=False)


# --- steps ---


yt_dlp_upgraded = False


def download(url: str, name: str | None = None) -> str:
    def yt_dlp(*args: str) -> str:
        global yt_dlp_upgraded
        cmd = ["yt-dlp", "--no-playlist", *args]
        result = subprocess.run(cmd, capture_output=True, text=True)
        # YouTube breaks yt-dlp often: on failure, upgrade to the latest nightly once per process and retry.
        if result.returncode and not yt_dlp_upgraded:
            yt_dlp_upgraded = True
            print(f"yt-dlp failed, upgrading and retrying: {result.stderr.strip()}")
            subprocess.run([sys.executable, "-m", *YT_DLP_INSTALL], check=True)
            result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError(f"yt-dlp failed: {result.stderr.strip()}")
        return result.stdout.strip()

    raw = name or clean_title(yt_dlp("--print", "title", url))
    name = to_file_name(raw)
    if not name:
        raise ValueError("Could not derive a file name")

    if not os.path.exists(f"{DATA_DIR}/meta/{name}.json"):
        update_meta(name, **to_meta(raw))

    dest = f"{DATA_DIR}/audio/{name}.mp3"
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
        source_dest = f"{DATA_DIR}/source/{os.path.basename(src)}"
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
    matches = glob.glob(f"{DATA_DIR}/source/{glob.escape(name)}.*")
    return matches[0] if matches else None


def separate(name: str, model: str = SEPARATION_MODEL, force: bool = False) -> str:
    from audio_separator.separator import Separator

    out_dir = f"{DATA_DIR}/stems/{name}" if model == SEPARATION_MODEL else f"{DATA_DIR}/compare/{model}/{name}"
    if os.path.exists(f"{out_dir}/vocals.mp3") and not force:
        print(f"{name} already separated, skipping")
        return out_dir

    source = find_source(name) or f"{DATA_DIR}/audio/{name}.mp3"
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

    dest = f"{DATA_DIR}/transcripts/{name}.json"
    # The vocal stem: no backing track to mishear, and timestamps match the stems the app plays.
    audio = f"{DATA_DIR}/stems/{name}/vocals.mp3"
    if not os.path.exists(audio):
        audio = f"{DATA_DIR}/audio/{name}.mp3"
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
    print(f"Saved {dest} ({transcription.language}, from {os.path.relpath(audio, DATA_DIR)})")
    return dest


def verify_lyrics(name: str) -> str | None:
    import lyrics

    with open(f"{DATA_DIR}/transcripts/{name}.json") as f:
        transcript = json.load(f)
    result = lyrics.verify(transcript, read_meta(name))
    if not result or not result["segments"]:
        update_meta(name, lyrics="none")
        print(f"No reference lyrics for {name}")
        return None
    dest = f"{DATA_DIR}/lyrics/{name}.json"
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "w") as f:
        json.dump(result, f, ensure_ascii=False)
    update_meta(name, lyrics="synced" if result["synced"] else "plain")
    print(f"Saved {dest} (lrclib {result['lrclibId']}, synced={result['synced']}, offset={result['offset']})")
    return dest


# Fully processed songs only: what GET /songs lists.
def list_songs() -> list[dict]:
    stems = f"{DATA_DIR}/stems"
    ready = [
        name
        for name in sorted(os.listdir(stems) if os.path.isdir(stems) else [])
        if all(
            os.path.exists(p)
            for p in [
                f"{stems}/{name}/vocals.mp3",
                f"{stems}/{name}/no_vocals.mp3",
                f"{DATA_DIR}/transcripts/{name}.json",
            ]
        )
    ]
    return [{"id": name, **read_meta(name)} for name in ready]
