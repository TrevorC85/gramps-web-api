#!/usr/bin/env bash
# Builds the Collins Gramps Web image from two committed trees, pinned by
# commit SHA. Local only: nothing is pushed.
#
#   collins-family/image/build.sh [BACKEND_REF] [FRONTEND_REF]
#
# BACKEND_REF  commit of the gramps-web-api fork (default: HEAD of this repo)
# FRONTEND_REF commit of the gramps-web fork in $FRONTEND_REPO
#              (default: HEAD of /workspace/gramps-web-frontend)
#
# Tags grampsweb-collins:26.11.2-<backend7>-<frontend7> and prints the image
# ID. Uncommitted changes are never included (git archive of the commits).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
BACKEND_REPO="$(git -C "$HERE" rev-parse --show-toplevel)"
FRONTEND_REPO="${FRONTEND_REPO:-/workspace/gramps-web-frontend}"
BACKEND_SHA="$(git -C "$BACKEND_REPO" rev-parse --verify "${1:-HEAD}^{commit}")"
FRONTEND_SHA="$(git -C "$FRONTEND_REPO" rev-parse --verify "${2:-HEAD}^{commit}")"
TAG="grampsweb-collins:26.11.2-${BACKEND_SHA:0:7}-${FRONTEND_SHA:0:7}"

CTX="$(mktemp -d)"
trap 'rm -rf "$CTX"' EXIT
mkdir -p "$CTX/backend" "$CTX/frontend"
git -C "$BACKEND_REPO" archive "$BACKEND_SHA" \
  pyproject.toml setup.py setup.cfg MANIFEST.in README.md LICENSE \
  alembic.ini alembic_users docker-entrypoint.sh gramps_webapi \
  | tar -x -C "$CTX/backend"
git -C "$FRONTEND_REPO" archive "$FRONTEND_SHA" | tar -x -C "$CTX/frontend"
cp "$HERE/Dockerfile" "$HERE/constraints-26.11.2.txt" "$CTX/"
# setuptools_scm-free version: gramps_webapi/_version.py is in the archive

echo "backend  $BACKEND_SHA"
echo "frontend $FRONTEND_SHA"
DOCKER_BUILDKIT=0 docker build \
  --build-arg BACKEND_SHA="$BACKEND_SHA" \
  --build-arg FRONTEND_SHA="$FRONTEND_SHA" \
  -t "$TAG" "$CTX"
docker image inspect --format '{{.Id}} {{.Size}}' "$TAG"
echo "$TAG"
