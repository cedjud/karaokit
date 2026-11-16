#!/bin/sh
# Mounts the R2 bucket at $DATA_DIR when R2_BUCKET is set (prod, see compose.prod.yaml), then starts the worker.
# Without it DATA_DIR stays a plain directory (local compose).
set -e

if [ -n "$R2_BUCKET" ]; then
  export RCLONE_CONFIG_R2_TYPE=s3
  export RCLONE_CONFIG_R2_PROVIDER=Cloudflare
  export RCLONE_CONFIG_R2_ACCESS_KEY_ID="$R2_ACCESS_KEY_ID"
  export RCLONE_CONFIG_R2_SECRET_ACCESS_KEY="$R2_SECRET_ACCESS_KEY"
  export RCLONE_CONFIG_R2_ENDPOINT="https://$R2_ACCOUNT_ID.r2.cloudflarestorage.com"
  mkdir -p "$DATA_DIR"
  # Bucket writes are sequential-only: buffer them on local disk and upload on close.
  rclone mount "r2:$R2_BUCKET" "$DATA_DIR" --daemon --vfs-cache-mode writes --dir-cache-time 30s --no-modtime
  for _ in $(seq 30); do
    mountpoint -q "$DATA_DIR" && break
    sleep 1
  done
  mountpoint -q "$DATA_DIR" || { echo "R2 mount failed" >&2; exit 1; }
fi

exec "$@"
