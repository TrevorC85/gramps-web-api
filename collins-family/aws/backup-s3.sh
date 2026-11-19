#!/bin/bash
# Nightly backup: tar the Gramps data directory and upload it to the Lightsail bucket
# as ONE object (backups/latest.tgz), replacing the previous one.
# Run by gramps-backup.timer; by hand: sudo /opt/gramps-web/collins-family/aws/backup-s3.sh
#   FORCE_BACKUP=1 skips the "much smaller than last time" safety check.
# The app and worker are stopped while tar runs (usually well under a minute) so the
# SQLite databases are copied in a consistent state; checks and upload run after restart.
# Because there is only one copy, a new archive must pass checks before it replaces it.
set -euo pipefail
# shellcheck source=/dev/null
. "${GRAMPS_ENV_FILE:-/etc/gramps-web/env}"
: "${BACKUP_BUCKET:?}" "${BACKUP_KEY:?}" "${AWS_REGION:?}"
DATA_ROOT="${DATA_ROOT:-/srv/gramps}"
SEED_KEY="${SEED_KEY:-}"
MIN_RATIO_PCT="${MIN_RATIO_PCT:-50}"

[ -d "$DATA_ROOT/data/grampsdb" ] || { echo "ERROR: $DATA_ROOT/data/grampsdb is missing" >&2; exit 1; }

tmp=$(mktemp /var/tmp/gramps-backup.XXXXXX.tgz)
check=$(mktemp -d /var/tmp/gramps-check.XXXXXX)
stopped=()

restart_app() {
  if [ "${#stopped[@]}" -gt 0 ]; then
    docker start "${stopped[@]}" >/dev/null || true
    stopped=()
  fi
}
cleanup() { restart_app; rm -rf "$tmp" "$check"; }
trap cleanup EXIT

for c in grampsweb_celery grampsweb; do
  if [ "$(docker inspect -f '{{.State.Running}}' "$c" 2>/dev/null || true)" = "true" ]; then
    docker stop -t 60 "$c" >/dev/null
    stopped+=("$c")
  fi
done

# Same layout as the local scripts/backup.sh (paths start with data/), minus rebuildable dirs.
tar -C "$DATA_ROOT" \
  --exclude='data/tmp' --exclude='data/cache' \
  --exclude='data/thumbnail_cache' --exclude='data/redis' \
  -czf "$tmp" data

restart_app

# ---- checks before the only backup is replaced
gzip -t "$tmp"
listing=$(tar -tzf "$tmp")
grep -qE '^data/users/users\.sqlite$' <<<"$listing" || { echo "ERROR: archive has no users.sqlite" >&2; exit 1; }
grep -qE '^data/grampsdb/[^/]+/sqlite\.db$' <<<"$listing" || { echo "ERROR: archive has no tree database" >&2; exit 1; }
unset listing
tar -C "$check" -xzf "$tmp" --wildcards 'data/users/users.sqlite' 'data/grampsdb/*/sqlite.db'
while IFS= read -r -d '' db; do
  python3 - "$db" <<'PY'
import sqlite3, sys
try:
    con = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
    res = con.execute("PRAGMA quick_check").fetchone()[0]
except sqlite3.Error as exc:
    res = str(exc)
if res != "ok":
    sys.exit(f"ERROR: {sys.argv[1]}: {res}")
PY
done < <(find "$check" -name '*.sqlite' -print0 -o -name 'sqlite.db' -print0)

size=$(stat -c %s "$tmp")
prev=$(aws s3api head-object --region "$AWS_REGION" --bucket "$BACKUP_BUCKET" --key "$BACKUP_KEY" \
  --query ContentLength --output text 2>/dev/null || echo 0)
if [ "${FORCE_BACKUP:-0}" != "1" ] && [ "$prev" -gt 0 ] && [ $((size * 100)) -lt $((prev * MIN_RATIO_PCT)) ]; then
  echo "ERROR: new backup is ${size} bytes, under ${MIN_RATIO_PCT}% of the current one (${prev} bytes)." >&2
  echo "Not replacing it. If the tree really shrank, run again with FORCE_BACKUP=1." >&2
  exit 1
fi

sum=$(sha256sum "$tmp" | cut -d' ' -f1)
# A PUT replaces the object only once the whole upload has succeeded.
aws s3 cp "$tmp" "s3://${BACKUP_BUCKET}/${BACKUP_KEY}" --region "$AWS_REGION" \
  --metadata "sha256=${sum},created=$(date -u +%FT%TZ)" --only-show-errors
echo "backup uploaded: s3://${BACKUP_BUCKET}/${BACKUP_KEY} ($(du -h "$tmp" | cut -f1), sha256 ${sum})"

# Keep just one copy: once a good nightly backup exists, the seed is no longer needed.
if [ -n "$SEED_KEY" ] && aws s3api head-object --region "$AWS_REGION" --bucket "$BACKUP_BUCKET" --key "$SEED_KEY" >/dev/null 2>&1; then
  aws s3 rm "s3://${BACKUP_BUCKET}/${SEED_KEY}" --region "$AWS_REGION" --only-show-errors
  echo "seed s3://${BACKUP_BUCKET}/${SEED_KEY} deleted (latest backup replaces it)"
fi
