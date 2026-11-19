#!/bin/bash
# Gramps Web on Lightsail: first-boot setup, safe to re-run (idempotent).
# Run as root. Called by the Lightsail launch script after the repo is cloned; re-run with
#   sudo /opt/gramps-web/collins-family/aws/bootstrap.sh
# Reads non-secret settings from /etc/gramps-web/env (written by the launch script).
set -euo pipefail

ENV_FILE="${GRAMPS_ENV_FILE:-/etc/gramps-web/env}"
# shellcheck source=/dev/null
. "$ENV_FILE"
: "${STACK_NAME:?}" "${AWS_REGION:?}" "${DOMAIN_NAME:?}" "${BACKUP_BUCKET:?}" "${BACKUP_KEY:?}"
: "${REPO_DIR:=/opt/gramps-web}" "${DATA_ROOT:=/srv/gramps}" "${SECRET_FILE:=/etc/gramps-web/secret-key}"
: "${SEED_KEY:=}" "${SEED_WAIT_MINUTES:=180}"
export AWS_REGION AWS_DEFAULT_REGION="$AWS_REGION"

AWS_DIR="$REPO_DIR/collins-family/aws"
COMPOSE_VERSION="v5.8.0"
# sha256 of docker-compose-linux-x86_64 v5.8.0, from the release's .sha256 file (checked 18 Nov 2026)
COMPOSE_SHA256="b8840f5008c447f41fce0a57ae3adeab42c89b1eb6e6012524bcabbfb4ffbaf6"
COMPOSE_PLUGIN=/usr/local/lib/docker/cli-plugins/docker-compose

log() { echo "[gramps-bootstrap $(date -u +%FT%TZ)] $*"; }

retry() {
  local n=0 max=$1; shift
  until "$@"; do
    n=$((n + 1))
    if [ "$n" -ge "$max" ]; then log "giving up: $*"; return 1; fi
    sleep $((n * 5))
  done
}

# ---------------------------------------------------------------- 1. packages
install_packages() {
  local arch
  arch=$(uname -m)
  if [ "$arch" != "x86_64" ]; then
    log "ERROR: expected an x86_64 Lightsail instance, got $arch"; exit 1
  fi
  if ! rpm -q docker >/dev/null 2>&1; then
    log "installing docker"
    retry 10 dnf install -y docker
  fi
  if ! command -v aws >/dev/null 2>&1; then
    log "installing AWS CLI"
    retry 10 dnf install -y awscli-2
  fi
  if ! "$COMPOSE_PLUGIN" version 2>/dev/null | grep -q "${COMPOSE_VERSION#v}"; then
    log "installing docker compose $COMPOSE_VERSION"
    local tmp
    tmp=$(mktemp)
    retry 5 curl -fsSL -o "$tmp" \
      "https://github.com/docker/compose/releases/download/${COMPOSE_VERSION}/docker-compose-linux-x86_64"
    echo "${COMPOSE_SHA256}  ${tmp}" | sha256sum -c -
    install -D -m 0755 "$tmp" "$COMPOSE_PLUGIN"
    rm -f "$tmp"
  fi
}

# ---------------------------------------------------------------- 2. swap (2 GB RAM bundle)
ensure_swap() {
  if [ ! -f /swapfile ]; then
    log "creating 2 GiB swapfile"
    dd if=/dev/zero of=/swapfile bs=1M count=2048 status=none
    chmod 600 /swapfile
    mkswap /swapfile >/dev/null
  fi
  grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap defaults 0 0' >> /etc/fstab
  swapon --show=NAME --noheadings | grep -qx /swapfile || swapon /swapfile
}

# ---------------------------------------------------------------- 3. data directory (instance SSD)
make_dirs() {
  local d
  install -d -m 0755 "$DATA_ROOT"
  for d in users indexdir thumbnail_cache cache secret grampsdb media tmp redis; do
    install -d -m 0755 "$DATA_ROOT/data/$d"
  done
  install -d -m 0700 "$DATA_ROOT/caddy/data" "$DATA_ROOT/caddy/config"
}

data_is_empty() {
  [ -z "$(ls -A "$DATA_ROOT/data/grampsdb" 2>/dev/null)" ] && \
    [ -z "$(ls -A "$DATA_ROOT/data/users" 2>/dev/null)" ]
}

s3_exists() {
  aws s3api head-object --bucket "$BACKUP_BUCKET" --key "$1" >/dev/null 2>&1
}

# A fresh server never starts empty when a seed is expected: an empty Gramps Web
# lets whoever opens it first create the owner account.
# Order: the bucket's latest nightly backup (rebuilt server), else the seed.
wait_for_bucket() {
  local i
  # Lightsail grants the instance bucket access only after the stack creates the
  # bucket, and the static IP swap briefly interrupts networking.
  for i in $(seq 1 $((SEED_WAIT_MINUTES * 2))); do
    if aws s3api head-bucket --bucket "$BACKUP_BUCKET" >/dev/null 2>&1; then return 0; fi
    [ $((i % 10)) -eq 1 ] && log "bucket $BACKUP_BUCKET not reachable yet; waiting"
    sleep 30
  done
  log "ERROR: bucket $BACKUP_BUCKET still not reachable after ${SEED_WAIT_MINUTES} min. The site is NOT started."
  exit 1
}

load_initial_data() {
  if ! data_is_empty || [ -e "$DATA_ROOT/.seeded" ]; then
    log "data directory already has a tree; not restoring"
    return 0
  fi
  wait_for_bucket
  if [ -z "$SEED_KEY" ] && ! s3_exists "$BACKUP_KEY"; then
    log "no seed configured and no backup in the bucket; starting with an empty site"
    date -u +%FT%TZ > "$DATA_ROOT/.seeded"
    return 0
  fi
  local deadline src=""
  deadline=$(( $(date +%s) + SEED_WAIT_MINUTES * 60 ))
  log "waiting for s3://${BACKUP_BUCKET}/${BACKUP_KEY} or s3://${BACKUP_BUCKET}/${SEED_KEY:-<none>} (up to ${SEED_WAIT_MINUTES} min)"
  while [ "$(date +%s)" -lt "$deadline" ]; do
    if s3_exists "$BACKUP_KEY"; then src="s3://${BACKUP_BUCKET}/${BACKUP_KEY}"; break; fi
    if [ -n "$SEED_KEY" ] && s3_exists "$SEED_KEY"; then
      # let a multi-part upload from the PC settle
      sleep 30
      src="s3://${BACKUP_BUCKET}/${SEED_KEY}"; break
    fi
    sleep 30
  done
  if [ -z "$src" ]; then
    log "ERROR: nothing to restore after ${SEED_WAIT_MINUTES} min. The site is NOT started."
    log "Upload the seed (see README), then run: sudo $AWS_DIR/bootstrap.sh"
    exit 1
  fi
  log "restoring $src"
  "$AWS_DIR/restore-s3.sh" "$src" --yes
  date -u +%FT%TZ > "$DATA_ROOT/.seeded"
}

# ---------------------------------------------------------------- 4. secret key (root-only file)
# GRAMPSWEB_SECRET_KEY lives only in this file (mode 600), not in the bucket or backups.
# A rebuilt server makes a new one: everyone just logs in again.
ensure_secret() {
  if [ -s "$SECRET_FILE" ]; then
    log "secret key file exists"
  else
    log "creating secret key file $SECRET_FILE"
    install -d -m 0755 "$(dirname "$SECRET_FILE")"
    ( umask 077; openssl rand -base64 48 | tr -d '\n/+=' > "$SECRET_FILE" )
  fi
  chown root:root "$SECRET_FILE"
  chmod 600 "$SECRET_FILE"
}

# ---------------------------------------------------------------- 5. systemd units
install_units() {
  install -m 0644 "$AWS_DIR/systemd/gramps-web.service" /etc/systemd/system/gramps-web.service
  install -m 0644 "$AWS_DIR/systemd/gramps-backup.service" /etc/systemd/system/gramps-backup.service
  install -m 0644 "$AWS_DIR/systemd/gramps-backup.timer" /etc/systemd/system/gramps-backup.timer
  rm -f /etc/systemd/system/docker.service.d/10-gramps-data.conf
  chmod 0755 "$AWS_DIR"/*.sh
  systemctl daemon-reload
}

main() {
  log "bootstrap start (stack $STACK_NAME, region $AWS_REGION)"
  install_packages
  ensure_swap
  make_dirs
  systemctl enable --now docker.service
  ensure_secret
  install_units
  load_initial_data
  systemctl enable gramps-backup.timer
  systemctl start gramps-backup.timer
  systemctl enable gramps-web.service
  log "pulling images and starting the stack"
  retry 5 systemctl restart gramps-web.service
  log "bootstrap done. Site: https://${DOMAIN_NAME}/ (needs DNS -> static IP)"
}

main "$@"
