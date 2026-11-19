#!/bin/bash
# Backup the local Gramps Web data directory (SQLite tree, user DB, media, caches).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STAMP="$(date +%Y-%m-%d_%H%M%S)"
DEST="${1:-$ROOT/backups/gramps-web-$STAMP.tgz}"
mkdir -p "$(dirname "$DEST")"
# Pause writes briefly if compose is available; backup still works while running.
# Exclude container-owned runtime dirs (redis dump, caches, tmp) that the host user cannot read.
# --ignore-failed-read keeps a usable archive if a locked file is still encountered.
tar --ignore-failed-read \
  --exclude='data/tmp' --exclude='data/tmp/*' \
  --exclude='data/cache' --exclude='data/cache/*' \
  --exclude='data/thumbnail_cache' --exclude='data/thumbnail_cache/*' \
  --exclude='data/redis' --exclude='data/redis/*' \
  -C "$ROOT" -czf "$DEST" data docker-compose.yml .env frontend-config.js test_3gen.ged patches
# .admin_password is included only if you pass --with-passwords
if [[ "${2:-}" == "--with-passwords" ]]; then
  tmp="${DEST%.tgz}-full.tgz"
  tar --ignore-failed-read \
    --exclude='data/tmp' --exclude='data/tmp/*' \
    --exclude='data/cache' --exclude='data/cache/*' \
    --exclude='data/thumbnail_cache' --exclude='data/thumbnail_cache/*' \
    --exclude='data/redis' --exclude='data/redis/*' \
    -C "$ROOT" -czf "$tmp" data docker-compose.yml .env .admin_password frontend-config.js test_3gen.ged
  echo "Wrote $tmp (includes .admin_password)"
fi
echo "Wrote $DEST"
ls -lh "$DEST"
