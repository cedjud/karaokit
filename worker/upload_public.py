"""One-off: copy ./public/{audio,stems,transcripts,meta} into R2, skipping files already there.

Run from the repo root: modal run worker/upload_public.py
"""

import os
import shutil

import modal

R2_ACCOUNT_ID = "e9a4adf8f5a2233cc086a8c96dc7e03a"
R2_BUCKET = "music-wheel"
MOUNT = "/data"
SRC = "/src"
DIRS = ["audio", "stems", "transcripts", "meta"]

app = modal.App("music-wheel-upload")

r2 = modal.CloudBucketMount(
    bucket_name=R2_BUCKET,
    bucket_endpoint_url=f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
    secret=modal.Secret.from_name("r2-credentials"),
)

image = modal.Image.debian_slim(python_version="3.12")
for d in DIRS:
    image = image.add_local_dir(f"public/{d}", f"{SRC}/{d}", ignore=["**/.DS_Store"])


@app.function(image=image, volumes={MOUNT: r2}, timeout=1800)
def upload():
    for d in DIRS:
        for root, _, files in os.walk(f"{SRC}/{d}"):
            for f in files:
                # public/audio has a stray transcript; only mp3s belong there.
                if d == "audio" and not f.endswith(".mp3"):
                    continue
                src = os.path.join(root, f)
                dest = os.path.join(MOUNT, os.path.relpath(src, SRC))
                if os.path.exists(dest):
                    print(f"skip {dest}")
                    continue
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                shutil.copyfile(src, dest)
                print(f"saved {dest}")
