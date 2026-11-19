#!/bin/bash
# Restore the Gramps data directory from a backup tarball in the Lightsail bucket.
#   sudo restore-s3.sh latest              # the bucket's backups/latest.tgz
#   sudo restore-s3.sh seed                # the seed object (if it is still there)
#   sudo restore-s3.sh s3://bucket/key.tgz # any archive (nightly or local scripts/backup.sh)
#   add --yes to skip the confirmation prompt
# The current data directory is kept as data.pre-restore-<time> (delete it once happy).
# Only the data/ part of the archive is restored; anything else in it (.env, compose
# files, build data from a local backup) is ignored.
set -euo pipefail
# shellcheck source=/dev/null
. "${GRAMPS_ENV_FILE:-/etc/gramps-web/env}"
: "${BACKUP_BUCKET:?}" "${BACKUP_KEY:?}" "${AWS_REGION:?}"
DATA_ROOT="${DATA_ROOT:-/srv/gramps}"

src="${1:-}"
yes="${2:-}"
[ -n "$src" ] || { echo "usage: $0 latest|seed|s3://bucket/key.tgz [--yes]" >&2; exit 2; }
install -d -m 0755 "$DATA_ROOT"

case "$src" in
  latest) src="s3://${BACKUP_BUCKET}/${BACKUP_KEY}" ;;
  seed)
    [ -n "${SEED_KEY:-}" ] || { echo "ERROR: no SEED_KEY configured" >&2; exit 2; }
    src="s3://${BACKUP_BUCKET}/${SEED_KEY}" ;;
esac
case "$src" in s3://*) ;; *) echo "ERROR: not an s3:// URI: $src" >&2; exit 2 ;; esac

if [ "$yes" != "--yes" ]; then
  read -r -p "Replace ${DATA_ROOT}/data with ${src}? [y/N] " answer
  [ "$answer" = "y" ] || [ "$answer" = "Y" ] || { echo "aborted"; exit 1; }
fi

tmp=$(mktemp /var/tmp/gramps-restore.XXXXXX.tgz)
stage=$(mktemp -d "${DATA_ROOT}/.restore.XXXXXX")
cleanup() { rm -rf "$tmp" "$stage"; }
trap cleanup EXIT

echo "downloading $src"
aws s3 cp "$src" "$tmp" --region "$AWS_REGION" --only-show-errors

# List once (grep -q on a live tar pipe trips pipefail with SIGPIPE).
listing=$(tar -tzf "$tmp")
# Refuse archives with absolute paths or '..' components.
if grep -qE '(^/|(^|/)\.\.(/|$))' <<<"$listing"; then
  echo "ERROR: archive contains unsafe paths" >&2; exit 1
fi
if grep -qE '^data/' <<<"$listing"; then member=data
elif grep -qE '^\./data/' <<<"$listing"; then member=./data
else echo "ERROR: archive has no data/ directory" >&2; exit 1
fi
unset listing

echo "extracting"
tar -C "$stage" --no-same-owner -xzf "$tmp" "$member"
[ -d "$stage/data" ] || { echo "ERROR: extract produced no data/" >&2; exit 1; }

was_active=false
if systemctl is-active --quiet gramps-web.service; then
  was_active=true
  systemctl stop gramps-web.service
fi

if [ -d "$DATA_ROOT/data" ]; then
  keep="${DATA_ROOT}/data.pre-restore-$(date -u +%Y%m%dT%H%M%SZ)"
  mv "$DATA_ROOT/data" "$keep"
  if [ -z "$(find "$keep" -type f -print -quit)" ]; then
    rm -rf "$keep"   # it held only empty directories (fresh server)
  else
    echo "previous data kept at $keep"
  fi
fi
mv "$stage/data" "$DATA_ROOT/data"
for d in users indexdir thumbnail_cache cache secret grampsdb media tmp redis; do
  install -d -m 0755 "$DATA_ROOT/data/$d"
done

if [ "$was_active" = true ]; then
  systemctl start gramps-web.service
fi
echo "restored $src into $DATA_ROOT/data"
