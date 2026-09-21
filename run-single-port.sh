#!/usr/bin/env bash
#
# Build the frontend, then serve the whole application from one port.
#
# http://localhost:24601 gives both the UI and the API -- no Node process left
# running. For day-to-day development use two ports instead (`npm run dev` on
# :5173 alongside `python server.py`), which keeps hot reload.
#
# Usage:  ./run-single-port.sh [--skip-build]
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKIP_BUILD=0

for arg in "$@"; do
  case "$arg" in
    --skip-build) SKIP_BUILD=1 ;;
    -h|--help) sed -n '2,12p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

if [ "$SKIP_BUILD" -eq 0 ]; then
  echo "==> Building the frontend"
  cd "$PROJECT_ROOT/frontend"
  [ -d node_modules ] || npm install
  npm run build
else
  echo "==> Skipping the frontend build (--skip-build)"
fi

if [ ! -f "$PROJECT_ROOT/frontend/dist/index.html" ]; then
  echo "No frontend/dist/index.html. Run without --skip-build." >&2
  exit 1
fi

cd "$PROJECT_ROOT/backend"

if [ ! -x .venv/bin/python ]; then
  echo "No backend/.venv. Create it and install requirements.txt first." >&2
  exit 1
fi

echo "==> Applying migrations"
CONFIG_PATH=conf .venv/bin/alembic upgrade head

echo "==> Starting the backend (UI + API on one port)"
exec env CONFIG_PATH=conf .venv/bin/python server.py
