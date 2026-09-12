#!/bin/sh
# Dev loop: a mock Syncthing with sample data, and the app pointed at it.
#   sh tests/dev.sh            -> UI on http://127.0.0.1:8080
set -e
cd "$(dirname "$0")/.."

MOCK_PORT="${MOCK_PORT:-18384}"
PORT="${PORT:-8080}"
export MOCK_PORT PORT

python3 tests/mock_syncthing.py &
MOCK_PID=$!
trap 'kill $MOCK_PID 2>/dev/null' EXIT INT TERM
sleep 0.5

SYNCTHING_URL="http://127.0.0.1:${MOCK_PORT}" \
SYNCTHING_API_KEY="${MOCK_API_KEY:-test}" \
BIND=127.0.0.1 \
LOG_LEVEL="${LOG_LEVEL:-INFO}" \
exec python3 -m app
