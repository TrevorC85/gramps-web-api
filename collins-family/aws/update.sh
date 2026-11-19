#!/bin/bash
# Update the deployment from git (compose file, patch, scripts, pinned image tags)
# and restart. Takes a backup first.
#   sudo /opt/gramps-web/collins-family/aws/update.sh
set -euo pipefail
# shellcheck source=/dev/null
. "${GRAMPS_ENV_FILE:-/etc/gramps-web/env}"
: "${REPO_DIR:=/opt/gramps-web}" "${REPO_BRANCH:?}"
AWS_DIR="$REPO_DIR/collins-family/aws"

"$AWS_DIR/backup-s3.sh"
git -C "$REPO_DIR" fetch --depth 1 origin "$REPO_BRANCH"
git -C "$REPO_DIR" checkout -q -B "$REPO_BRANCH" FETCH_HEAD
# Re-run the idempotent bootstrap: reinstalls units and restarts the stack.
exec "$AWS_DIR/bootstrap.sh"
