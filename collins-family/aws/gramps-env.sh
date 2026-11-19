#!/bin/bash
# Writes /run/gramps-web/compose.env (tmpfs, mode 600) for docker compose and keeps
# containers away from the instance metadata service. Called by gramps-web.service
# (ExecStartPre). The secret key is read from the root-only file SECRET_FILE.
set -euo pipefail
# shellcheck source=/dev/null
. "${GRAMPS_ENV_FILE:-/etc/gramps-web/env}"
: "${DOMAIN_NAME:?}" "${ACME_EMAIL:?}"
DATA_ROOT="${DATA_ROOT:-/srv/gramps}"
SECRET_FILE="${SECRET_FILE:-/etc/gramps-web/secret-key}"

[ -d "$DATA_ROOT/data" ] || { echo "ERROR: $DATA_ROOT/data is missing (run bootstrap.sh)" >&2; exit 1; }
[ -s "$SECRET_FILE" ] || { echo "ERROR: $SECRET_FILE is missing (run bootstrap.sh)" >&2; exit 1; }

site_hosts="$DOMAIN_NAME"
if [ "${WWW_ENABLED:-false}" = "true" ] && [ -n "${HOSTED_ZONE_ID:-}" ]; then
  site_hosts="$DOMAIN_NAME www.$DOMAIN_NAME"
fi

# The instance metadata service hands out the bucket credentials; containers must
# not reach it (Docker's DOCKER-USER chain filters container traffic only).
if command -v iptables >/dev/null 2>&1 && iptables -L DOCKER-USER -n >/dev/null 2>&1; then
  iptables -C DOCKER-USER -d 169.254.169.254/32 -j REJECT 2>/dev/null || \
    iptables -I DOCKER-USER -d 169.254.169.254/32 -j REJECT
else
  echo "WARNING: could not add the DOCKER-USER rule that blocks containers from 169.254.169.254" >&2
fi
if command -v ip6tables >/dev/null 2>&1 && ip6tables -L DOCKER-USER -n >/dev/null 2>&1; then
  ip6tables -C DOCKER-USER -d fd00:ec2::254/128 -j REJECT 2>/dev/null || \
    ip6tables -I DOCKER-USER -d fd00:ec2::254/128 -j REJECT
fi

install -d -m 0700 /run/gramps-web
umask 077
tmp=$(mktemp /run/gramps-web/compose.env.XXXXXX)
{
  printf 'DOMAIN_NAME=%s\n' "$DOMAIN_NAME"
  printf 'SITE_HOSTS=%s\n' "$site_hosts"
  printf 'ACME_EMAIL=%s\n' "$ACME_EMAIL"
  printf 'GRAMPS_DATA_DIR=%s\n' "$DATA_ROOT/data"
  printf 'CADDY_DATA_DIR=%s\n' "$DATA_ROOT/caddy"
  printf 'GRAMPSWEB_SECRET_KEY=%s\n' "$(cat "$SECRET_FILE")"
} > "$tmp"
mv -f "$tmp" /run/gramps-web/compose.env
