#!/usr/bin/env bash
# ResumeMatch Coach - start the app (macOS / Linux)
#
# One process, one port: FastAPI serves the built React UI itself, so there is
# no second dev server to keep alive. For hot reload while developing the UI,
# run `npm run dev` in frontend-react/ instead (port 5173, API proxied to 8000).
#
#   ./run.sh           start on http://127.0.0.1:8000
#   ./run.sh --check   probe the providers first, then start
#   ./run.sh --build   rebuild the React UI, then start
#
# The React UI is served from frontend-react/dist. If that is missing, the
# backend falls back to the dependency-free single page in frontend/.

set -euo pipefail
cd "$(dirname "$0")"

PY=".venv/bin/python"
if [ ! -x "$PY" ]; then
  echo "No virtual environment found. Creating one..."
  python3 -m venv .venv
  "$PY" -m pip install --upgrade pip --quiet
  "$PY" -m pip install -r requirements.txt --quiet
  echo "Dependencies installed."
fi

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env from .env.example - add your API keys."
  echo "(The app still runs with no keys, using the local keyword fallback.)"
fi

# Build the React UI if it has never been built, or if --build was passed.
if [ "${1:-}" = "--build" ] || [ ! -f frontend-react/dist/index.html ]; then
  if command -v npm >/dev/null 2>&1; then
    ( cd frontend-react
      [ -d node_modules ] || { echo "Installing React dependencies..."; npm install --silent; }
      echo "Building the React UI..."
      npm run build )
  else
    echo "npm not found - using the plain HTML UI instead."
  fi
fi

if [ "${1:-}" = "--check" ]; then
  echo
  "$PY" scripts/check_providers.py
fi

# Loopback only. Binding 0.0.0.0 would expose your analysis history to the
# whole local network - see DESIGN.md s17.
echo
echo "  ResumeMatch Coach  ->  http://127.0.0.1:8000"
echo "  API docs           ->  http://127.0.0.1:8000/docs"
echo "  Ctrl+C to stop"
echo

exec "$PY" -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
