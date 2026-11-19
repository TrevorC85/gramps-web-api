#!/bin/bash
# Local rehearsal of switching the site to our own image and back, with a backup and
# a restore drill. Runs a COPY of the local site's data on 127.0.0.1:5059 (valkey 6383);
# the local site (5055), its data and AWS are not touched. Uses aws/switch-image.sh exactly
# as the server would, with local BACKUP/RESTART commands and its default health check pointed at 5059.
#
#   collins-family/image/rehearse-local.sh <our-image-tag> [rehearsal-dir]
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
CF="$(cd "$HERE/.." && pwd)"
TAG="${1:?usage: rehearse-local.sh <our-image-tag> [dir]}"
R="${2:-/workspace/gramps-web-proto/rehearsal}"
SITE=/workspace/gramps-web            # local site (read only)
STOCK='ghcr.io/gramps-project/grampsweb:26.11.2@sha256:80a1d75ca0f67f9203aecca60f7639312a2769fe0af45b34608b0add64f7c07f'
URL=http://127.0.0.1:5059
log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$R/rehearsal.log" >&2; }

[ ! -e "$R/data" ] || { echo "$R/data exists; remove it first" >&2; exit 1; }
mkdir -p "$R/data"/{grampsdb,users,indexdir,thumbnail_cache,cache,secret,tmp,redis} "$R/backups"
: > "$R/rehearsal.log"

log "copy of the local data (sqlite online backup, read only)"
python3 - "$SITE/data" "$R/data" <<'PY'
import os, shutil, sqlite3, sys
src, dst = sys.argv[1:]
def copy_db(a, b):
    s = sqlite3.connect(f"file:{a}?mode=ro", uri=True); d = sqlite3.connect(b)
    s.backup(d); s.close(); d.close()
for tree in os.listdir(f"{src}/grampsdb"):
    os.makedirs(f"{dst}/grampsdb/{tree}", exist_ok=True)
    for name in os.listdir(f"{src}/grampsdb/{tree}"):
        a, b = f"{src}/grampsdb/{tree}/{name}", f"{dst}/grampsdb/{tree}/{name}"
        if name.endswith(".db"):
            copy_db(a, b)
        else:
            shutil.copy2(a, b)
copy_db(f"{src}/users/users.sqlite", f"{dst}/users/users.sqlite")
PY

# The draft local compose file, moved to rehearsal ports and names; media read only
sed -e 's/5055/5059/g' -e 's/6379/6383/g' \
    -e 's/container_name: grampsweb/container_name: rehearsal_grampsweb/' \
    -e "s#./data/media:/app/media#$SITE/data/media:/app/media:ro#" \
    -e "s#./frontend-config.js#$CF/frontend-config.js#" \
    -e "s#./patches/#$CF/patches/#" \
    -e '/env_file:/,+1d' \
    "$CF/docker-compose.yml" > "$R/docker-compose.yml"
sed -i 's/container_name: rehearsal_grampsweb_redis/container_name: rehearsal_grampsweb_redis/' "$R/docker-compose.yml"
cat > "$R/env" <<ENV
GRAMPSWEB_IMAGE=''
ENV

export GRAMPS_ENV_FILE="$R/env"
export RESTART_CMD="set -a; . '$R/env'; set +a; docker compose -p rehearsal -f '$R/docker-compose.yml' up -d --force-recreate grampsweb grampsweb_redis >/dev/null 2>&1"
export CONTAINER=rehearsal_grampsweb API_URL=$URL HEALTH_TRIES=12   # default HEALTH_CMD of switch-image.sh
up() { for _ in $(seq 1 30); do [ "$(curl -s -o /dev/null -w '%{http_code}' $URL/api/metadata/)" = 401 ] && return 0; sleep 3; done; return 1; }
export BACKUP_CMD="tar -C '$R' -czf '$R/backups/latest.tgz' data/grampsdb data/users"

counts() { # people/families visible to the owner, from the API
  python3 - "$URL" <<'PY'
import json, sys, urllib.request
url = sys.argv[1]
user = pw = None
for line in open("/workspace/gramps-web/.admin_password"):
    k, _, v = line.strip().partition("=")
    if k == "username" and user is None: user = v
    elif k == "password" and user and pw is None: pw = v
req = urllib.request.Request(url + "/api/token/", json.dumps({"username": user, "password": pw}).encode(), {"Content-Type": "application/json"})
import time
for attempt in range(8):  # login is rate limited
    try:
        tok = json.load(urllib.request.urlopen(req))["access_token"]; break
    except urllib.error.HTTPError as e:
        if e.code != 429: raise
        time.sleep(15)
def get(p):
    r = urllib.request.Request(url + p, headers={"Authorization": "Bearer " + tok})
    try:
        resp = urllib.request.urlopen(r)
        return resp.status, json.load(resp)
    except urllib.error.HTTPError as e:
        return e.code, None
_, people = get("/api/people/?keys=handle")
_, fams = get("/api/families/?keys=handle")
st, _ = get("/api/childref-confidence/?person=I0009&generations=3")
print(f"people={len(people)} families={len(fams)} childref-confidence={st}")
PY
}
expect() { # expect CURRENT PREVIOUS: container and env file agree with what should happen
  local st; st=$("$CF/aws/switch-image.sh" status)
  [ "$(image_of)" = "$1" ] && [ "$st" = "$(printf 'GRAMPSWEB_IMAGE=%s\nPREV_GRAMPSWEB_IMAGE=%s' "$1" "$2")" ] \
    && log "   OK: running $1, rollback target ${2:-official}" \
    || { log "FAIL: expected $1 / $2, got container $(image_of) and: $st"; exit 1; }
}
image_of() { docker inspect --format '{{.Config.Image}}' rehearsal_grampsweb; }

log "1. start on the official image (as the site runs today)"
eval "$RESTART_CMD"
up
before=$(counts)
log "   image=$(image_of) $before"

log "2. tarball of our image, as uploaded to the bucket"
docker save "$TAG" | gzip -1 > "$R/image.tgz"
SUM=$(sha256sum "$R/image.tgz" | cut -d' ' -f1)
ID=$(docker image inspect --format '{{.Id}}' "$TAG")
log "   $(du -h "$R/image.tgz" | cut -f1) sha256=$SUM id=$ID"

log "3. refuse a tarball with a wrong sha256"
if "$CF/aws/switch-image.sh" use "$TAG" "$R/image.tgz" "$(printf '0%.0s' $(seq 64))" "$ID" 2>>"$R/rehearsal.log"; then
  log "   FAIL: accepted"; exit 1
fi
log "   refused; still image=$(image_of)"

log "4. switch to our image"
"$CF/aws/switch-image.sh" use "$TAG" "$R/image.tgz" "$SUM" "$ID" 2>>"$R/rehearsal.log"
log "   image=$(image_of) $(counts)"
expect "$TAG" "$STOCK"

log "4b. an image that does not start is switched back automatically"
printf 'FROM %s\nENTRYPOINT ["/bin/false"]\n' "$TAG" | DOCKER_BUILDKIT=0 docker build -q -t grampsweb-collins:rehearsal-broken - >/dev/null
docker save grampsweb-collins:rehearsal-broken | gzip -1 > "$R/broken.tgz"
if "$CF/aws/switch-image.sh" use grampsweb-collins:rehearsal-broken "$R/broken.tgz" \
     "$(sha256sum "$R/broken.tgz" | cut -d' ' -f1)" "$(docker image inspect --format '{{.Id}}' grampsweb-collins:rehearsal-broken)" 2>>"$R/rehearsal.log"; then
  log "   FAIL: broken image accepted"; exit 1
fi
up; log "   refused after the health check; $(counts)"
expect "$TAG" "$STOCK"
rm -f "$R/broken.tgz"; docker rmi grampsweb-collins:rehearsal-broken >/dev/null

log "5. roll back to the previous (official) image"
"$CF/aws/switch-image.sh" rollback 2>>"$R/rehearsal.log"
after=$(counts)
log "   image=$(image_of) $after"
expect "$STOCK" "$TAG"
[ "${before%% childref*}" = "${after%% childref*}" ] && log "   counts unchanged" || { log "FAIL: counts differ"; exit 1; }

log "6. restore drill from the backup taken before the last switch"
docker compose -p rehearsal -f "$R/docker-compose.yml" stop grampsweb >/dev/null 2>&1
sudo mv "$R/data/grampsdb" "$R/data/grampsdb.replaced"; sudo mv "$R/data/users" "$R/data/users.replaced"
tar -C "$R" -xzf "$R/backups/latest.tgz"
eval "$RESTART_CMD"
up
restored=$(counts)
log "   image=$(image_of) $restored"
expect "$STOCK" "$TAG"
[ "${before%% childref*}" = "${restored%% childref*}" ] && log "   restore OK" || { log "FAIL: restore counts differ"; exit 1; }

log "7. stop the rehearsal stack"
docker compose -p rehearsal -f "$R/docker-compose.yml" down >/dev/null 2>&1
log "rehearsal passed"
