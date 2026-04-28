#!/usr/bin/env bash
# Run the voice satellite against local Docker services (laptop testing).
# Usage: ./run-local.sh [satellite-id]
#
# Requires Docker services running:
#   docker compose up -d whisper piper openwakeword

set -euo pipefail

SATELLITE_ID="${1:-laptop-test}"
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
ENV_FILE="$REPO_ROOT/.env"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "ERROR: $ENV_FILE not found" >&2
  exit 1
fi

export SATELLITE_ID
export SERVER_HOST="localhost"
export WAKE_HOST="localhost"
export STT_HOST="localhost"
export TTS_HOST="localhost"
export BACKEND_URL="http://localhost:8000"
export BACKEND_API_KEY
BACKEND_API_KEY="$(grep ^BACKEND_API_KEY "$ENV_FILE" | cut -d= -f2)"

cd "$(dirname "$0")"

echo "[run-local] satellite='$SATELLITE_ID' backend=$BACKEND_URL"
echo "[run-local] wake=localhost:10400  stt=localhost:10300  tts=localhost:10200"
echo "[run-local] Make sure 'docker compose up -d whisper piper openwakeword' is running."
echo ""

VENV="$(dirname "$0")/venv"
if [[ -f "$VENV/bin/python" ]]; then
  "$VENV/bin/python" satellite.py
else
  uv run python satellite.py
fi
