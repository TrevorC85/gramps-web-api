#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
docker compose down
echo "Gramps Web containers stopped. Data in $ROOT/data was kept."
