#!/usr/bin/env bash
# One command to run the whole thing.
#   ./run.sh            -> set up venv, start the gateway + dashboard on :8000
#   ./run.sh demo       -> same, then stream realistic traffic so it's alive
#   ./run.sh traffic    -> just send traffic to an already-running server
set -euo pipefail
cd "$(dirname "$0")"

PORT="${PORT:-8000}"
PY=".venv/bin/python"

ensure_venv() {
  if [ ! -d .venv ]; then
    echo "→ creating virtualenv…"
    python3 -m venv .venv
  fi
  echo "→ installing dependencies…"
  .venv/bin/pip install -q --upgrade pip
  .venv/bin/pip install -q -r requirements.txt
}

case "${1:-serve}" in
  traffic)
    exec $PY -m scripts.traffic --n "${2:-300}" --rps "${3:-12}" --outage
    ;;
  demo)
    ensure_venv
    echo "→ starting gateway on http://127.0.0.1:${PORT}"
    $PY -m uvicorn gateway.app:app --host 127.0.0.1 --port "$PORT" --log-level warning &
    SERVER_PID=$!
    trap 'kill $SERVER_PID 2>/dev/null || true' EXIT
    sleep 3
    echo "→ streaming demo traffic (open the dashboard now)…"
    $PY -m scripts.traffic --n 400 --rps 10 --outage || true
    echo "→ traffic done; gateway still live at http://127.0.0.1:${PORT}  (Ctrl-C to stop)"
    wait $SERVER_PID
    ;;
  serve|*)
    ensure_venv
    echo ""
    echo "  ┌─────────────────────────────────────────────────────────┐"
    echo "  │  Intelligent LLM Gateway                                 │"
    echo "  │  dashboard → http://127.0.0.1:${PORT}                        │"
    echo "  │  API docs  → http://127.0.0.1:${PORT}/docs                   │"
    echo "  │  tip: in another terminal run  ./run.sh traffic          │"
    echo "  └─────────────────────────────────────────────────────────┘"
    echo ""
    exec $PY -m uvicorn gateway.app:app --host 127.0.0.1 --port "$PORT"
    ;;
esac
