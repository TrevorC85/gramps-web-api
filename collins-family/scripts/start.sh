#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
"$ROOT/scripts/ensure-dockerd.sh"
cd "$ROOT"
docker compose up -d
echo "Gramps Web should be at http://127.0.0.1:5055/ (login required)."
echo "Credentials are in $ROOT/.admin_password (mode 600)."
