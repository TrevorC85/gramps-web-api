#!/bin/bash
# Update the deployment from git (compose file, patch, scripts, pinned image tags)
# and restart. Takes a backup first.
#   sudo /opt/gramps-web/collins-family/aws/update.sh            # re-fetch the deployed ref
#   sudo /opt/gramps-web/collins-family/aws/update.sh <ref>      # move to a new branch, tag or
#                                                                # full 40-character commit SHA
# A 40-character SHA pins the deploy: the checked-out commit must match it exactly, or
# nothing is changed. The new ref is saved as REPO_BRANCH in /etc/gramps-web/env.
set -euo pipefail
# DRAFT: image switches are separate from code updates (see switch-image.sh):
#   update.sh image use <tag> <s3://bucket/images/x.tgz> <sha256> <image-id>
#   update.sh image rollback | status
if [ "${1:-}" = "image" ]; then
  shift
  exec "$(dirname "$0")/switch-image.sh" "$@"
fi
ENV_FILE="${GRAMPS_ENV_FILE:-/etc/gramps-web/env}"
# shellcheck source=/dev/null
. "$ENV_FILE"
: "${REPO_DIR:=/opt/gramps-web}" "${REPO_BRANCH:?}"
REF="${1:-$REPO_BRANCH}"
case "$REF" in
  ""|-*|*[!A-Za-z0-9._/-]*) echo "update.sh: invalid ref '$REF'" >&2; exit 2 ;;
esac
is_sha() { [ "${#1}" -eq 40 ] && case "$1" in *[!0-9a-f]*) return 1 ;; *) return 0 ;; esac; }

AWS_DIR="$REPO_DIR/collins-family/aws"
"$AWS_DIR/backup-s3.sh"

git -C "$REPO_DIR" fetch -q --depth 1 origin "$REF"
fetched=$(git -C "$REPO_DIR" rev-parse FETCH_HEAD)
if is_sha "$REF" && [ "$fetched" != "$REF" ]; then
  echo "FATAL: fetched commit $fetched does not match pinned $REF; nothing changed" >&2
  exit 1
fi
# Detached checkout: works the same for a branch, a tag or a commit SHA.
git -C "$REPO_DIR" checkout -q --detach FETCH_HEAD
head=$(git -C "$REPO_DIR" rev-parse HEAD)
if is_sha "$REF" && [ "$head" != "$REF" ]; then
  echo "FATAL: checked-out commit $head does not match pinned $REF" >&2
  exit 1
fi
echo "update.sh: now at $head (ref $REF)"
if [ "$REF" != "$REPO_BRANCH" ]; then
  sed -i "s|^REPO_BRANCH=.*|REPO_BRANCH='$REF'|" "$ENV_FILE"
fi
# Re-run the idempotent bootstrap: reinstalls units and restarts the stack.
exec "$AWS_DIR/bootstrap.sh"
