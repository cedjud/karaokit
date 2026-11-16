"""One-off: download every song's files from the public R2 URL into ./data (no credentials).

Public buckets can't be listed, so song ids come from GET <api>/songs and the paths from the
layout in worker/app.py. Skips files that already exist locally. Missing remote files (404) are ignored.

Usage (repo root):
  uv run scripts/pull-r2.py            # reads VITE_API_URL / VITE_MEDIA_URL from .env
  uv run scripts/pull-r2.py --dest data
"""

import argparse
import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--dest", default="data")
args = ap.parse_args()

env = {}
for line in Path(".env").read_text().splitlines():
    if "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip("\"'")
api, media = env["VITE_API_URL"].rstrip("/"), env["VITE_MEDIA_URL"].rstrip("/")


def get(url: str) -> bytes | None:
    req = urllib.request.Request(url, headers={"User-Agent": "music-wheel-pull"})
    try:
        with urllib.request.urlopen(req) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        if e.code in (403, 404):
            return None
        raise


songs = json.loads(get(f"{api}/songs") or b"[]")
dest = Path(args.dest)
got = skipped = missing = 0
for s in songs:
    i = s["id"]
    keys = [
        f"audio/{i}.mp3",
        f"stems/{i}/vocals.mp3",
        f"stems/{i}/no_vocals.mp3",
        f"transcripts/{i}.json",
        f"lyrics/{i}.json",
        f"meta/{i}.json",
    ] + [f"source/{i}.{ext}" for ext in ("opus", "m4a", "webm", "mp3", "mp4", "wav")]
    for key in keys:
        path = dest / key
        if path.exists():
            skipped += 1
            continue
        data = get(f"{media}/{urllib.parse.quote(key)}")
        if data is None:
            missing += 1
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        got += 1
        print(f"saved {key}")

print(f"{len(songs)} songs: {got} downloaded, {skipped} already local, {missing} not in bucket")
