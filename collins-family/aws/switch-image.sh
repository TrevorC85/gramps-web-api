#!/bin/bash
# Switch the Gramps Web image of the stack, or roll back.
#
#   switch-image.sh use <tag> <tarball> <sha256> <image-id>
#       <tarball>: docker save | gzip of the image, an s3://... URI (the stack's bucket) or
#       a local path. Its sha256 and the loaded image ID must match, or nothing changes.
#   switch-image.sh rollback      # back to the image in use before the last "use"
#   switch-image.sh status
#
# Every switch takes a backup first ($BACKUP_CMD) and records the previous image in the env
# file (PREV_GRAMPSWEB_IMAGE), then restarts the stack ($RESTART_CMD) and waits for
# $HEALTH_CMD. If the site does not come up, the previous image is restored automatically.
# The data format is unchanged by our image (same Gramps 6.0 database, same user-DB
# migrations as 26.11.2), so a rollback needs no data restore.
#
# Defaults are for the Lightsail server; the local rehearsal overrides them:
#   GRAMPS_ENV_FILE=/etc/gramps-web/env
#   BACKUP_CMD=/opt/gramps-web/collins-family/aws/backup-s3.sh
#   RESTART_CMD="systemctl restart gramps-web.service"
#   HEALTH_CMD=<command that succeeds when the site is up> (default: API inside $CONTAINER)
#   CONTAINER=grampsweb API_URL=http://127.0.0.1:5000
set -euo pipefail
ENV_FILE="${GRAMPS_ENV_FILE:-/etc/gramps-web/env}"
BACKUP_CMD="${BACKUP_CMD:-/opt/gramps-web/collins-family/aws/backup-s3.sh}"
RESTART_CMD="${RESTART_CMD:-systemctl restart gramps-web.service}"
CONTAINER="${CONTAINER:-grampsweb}"
API_URL="${API_URL:-http://127.0.0.1:5000}"   # as seen inside the container
# Default health check: the API answers inside the container. /api/metadata/ needs a login,
# so 401 means "up" (urlopen raises on it); refused connections and 5xx are failures.
DEFAULT_HEALTH="docker exec $CONTAINER python3 -c \"import sys,urllib.request,urllib.error
try: urllib.request.urlopen('$API_URL/api/metadata/',timeout=10)
except urllib.error.HTTPError as e: sys.exit(e.code!=401)\""
HEALTH_CMD="${HEALTH_CMD:-$DEFAULT_HEALTH}"
STOCK_IMAGE='ghcr.io/gramps-project/grampsweb:26.11.2@sha256:80a1d75ca0f67f9203aecca60f7639312a2769fe0af45b34608b0add64f7c07f'

log() { echo "switch-image: $*" >&2; }
# shellcheck source=/dev/null
. "$ENV_FILE"
current="${GRAMPSWEB_IMAGE:-$STOCK_IMAGE}"
previous="${PREV_GRAMPSWEB_IMAGE:-}"

set_env() { # set_env KEY VALUE (values are validated before)
  if grep -q "^$1=" "$ENV_FILE"; then
    sed -i "s|^$1=.*|$1='$2'|" "$ENV_FILE"
  else
    printf "%s='%s'\n" "$1" "$2" >> "$ENV_FILE"
  fi
}
valid_ref() { case "$1" in ""|-*|*[!A-Za-z0-9._/:@-]*) return 1 ;; esac; }

healthy() {
  for _ in $(seq 1 "${HEALTH_TRIES:-30}"); do
    if eval "$HEALTH_CMD" >/dev/null 2>&1; then return 0; fi
    sleep 5
  done
  return 1
}

running_is() { # the container really runs IMAGE (catches an env file compose did not read)
  local got
  got=$(docker inspect --format '{{.Config.Image}}' "$CONTAINER" 2>/dev/null || true)
  [ "$got" = "$1" ] || { log "container runs '$got', expected '$1'"; return 1; }
}

switch_to() { # switch_to IMAGE
  log "backup before switching"
  eval "$BACKUP_CMD"
  set_env PREV_GRAMPSWEB_IMAGE "$current"
  set_env GRAMPSWEB_IMAGE "$1"
  log "restarting with $1"
  if eval "$RESTART_CMD" && healthy && running_is "$1"; then
    log "site is up on $1 (previous: $current)"
    return 0
  fi
  log "site did not come up on $1; restoring $current"
  set_env GRAMPSWEB_IMAGE "$current"
  set_env PREV_GRAMPSWEB_IMAGE "$previous"   # a failed switch leaves the rollback target alone
  eval "$RESTART_CMD"
  healthy && log "back on $current" || log "WARNING: site not healthy on $current either"
  return 1
}

case "${1:-}" in
  use)
    [ $# -eq 5 ] || { log "usage: $0 use <tag> <tarball> <sha256> <image-id>"; exit 2; }
    tag="$2"; tarball="$3"; sum="$4"; id="$5"
    valid_ref "$tag" || { log "invalid tag"; exit 2; }
    case "$sum" in *[!0-9a-f]*|"") log "invalid sha256"; exit 2 ;; esac
    [ "${#sum}" -eq 64 ] || { log "invalid sha256"; exit 2; }
    # On disk, not /tmp (tmpfs in RAM on the server); about 0.5 GB
    tmp=$(mktemp -d -p "${IMAGE_TMP_DIR:-/var/tmp}"); trap 'rm -rf "$tmp"' EXIT
    case "$tarball" in
      s3://*) aws s3 cp --only-show-errors --region "${AWS_REGION:-us-west-2}" "$tarball" "$tmp/image.tgz" ;;
      *) cp "$tarball" "$tmp/image.tgz" ;;
    esac
    echo "$sum  $tmp/image.tgz" | sha256sum -c --quiet - \
      || { log "FATAL: tarball sha256 mismatch; nothing changed"; exit 1; }
    gunzip -c "$tmp/image.tgz" | docker load -q >/dev/null
    loaded=$(docker image inspect --format '{{.Id}}' "$tag")
    [ "$loaded" = "$id" ] || { log "FATAL: image $tag is $loaded, expected $id; nothing changed"; exit 1; }
    switch_to "$tag"
    ;;
  rollback)
    prev="${previous:-$STOCK_IMAGE}"
    docker image inspect "$prev" >/dev/null 2>&1 || docker pull -q "$prev" >/dev/null
    switch_to "$prev"
    ;;
  status)
    echo "GRAMPSWEB_IMAGE=$current"
    echo "PREV_GRAMPSWEB_IMAGE=$previous"
    ;;
  *) log "usage: $0 use <tag> <tarball> <sha256> <image-id> | rollback | status"; exit 2 ;;
esac
