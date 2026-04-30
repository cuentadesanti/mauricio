#!/usr/bin/env bash
# Deploy the Mauricio voice satellite to a Raspberry Pi.
#
# Usage:
#   ./scripts/deploy-satellite.sh [user@host] [satellite-id]
#
# Examples:
#   ./scripts/deploy-satellite.sh                          # pi@raspberrypi.local, id=living-room
#   ./scripts/deploy-satellite.sh pi@192.168.1.50          # custom IP
#   ./scripts/deploy-satellite.sh pi@192.168.1.50 bedroom  # custom satellite ID
#
# Requirements on this Mac:
#   - SSH access to the Pi (key-based auth recommended)
#   - Docker running locally (for the wyoming services)
#
# What this script does:
#   1. Creates ~/mauricio-satellite/ on the Pi
#   2. Installs system dependencies (portaudio, python3, uv) if needed
#   3. Syncs satellite.py and service file
#   4. Creates/updates Python venv with required packages
#   5. Writes .env with server address pointing back to this Mac
#   6. Installs and (re)starts the systemd service

set -euo pipefail

# ── Arguments ────────────────────────────────────────────────────────────────
PI="${1:-pi@raspberrypi.local}"
SATELLITE_ID="${2:-living-room}"
PI_USER="${PI%%@*}"
PI_HOST="${PI##*@}"
REMOTE_DIR="/home/${PI_USER}/mauricio-satellite"

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SAT_DIR="$REPO_ROOT/apps/voice-satellite"
ENV_FILE="$REPO_ROOT/.env"

# ── Detect this Mac's IP on the local network ────────────────────────────────
SERVER_IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo "")
if [[ -z "$SERVER_IP" ]]; then
  echo "ERROR: could not detect local IP. Set SERVER_IP manually in this script." >&2
  exit 1
fi

# ── Read BACKEND_API_KEY from .env ───────────────────────────────────────────
if [[ ! -f "$ENV_FILE" ]]; then
  echo "ERROR: $ENV_FILE not found — needed for BACKEND_API_KEY" >&2
  exit 1
fi
BACKEND_API_KEY=$(grep ^BACKEND_API_KEY "$ENV_FILE" | cut -d= -f2 | tr -d '"' | tr -d "'")
if [[ -z "$BACKEND_API_KEY" ]]; then
  echo "ERROR: BACKEND_API_KEY not found in $ENV_FILE" >&2
  exit 1
fi

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Mauricio satellite deploy"
echo "  Target : $PI ($REMOTE_DIR)"
echo "  ID     : $SATELLITE_ID"
echo "  Server : $SERVER_IP (this Mac)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ── 1. Check SSH connectivity ────────────────────────────────────────────────
echo ""
echo "[1/6] Checking SSH connectivity..."
if ! ssh -o ConnectTimeout=5 -o BatchMode=yes "$PI" "echo ok" &>/dev/null; then
  echo ""
  echo "Cannot connect to $PI. Quick fixes:"
  echo "  • Make sure the Pi is on and on the same network"
  echo "  • Try: ssh-copy-id $PI  (to set up key-based auth)"
  echo "  • Or pass the correct address: $0 pi@<ip-address>"
  exit 1
fi
echo "    Connected."

# ── 2. Install system dependencies (idempotent) ───────────────────────────────
echo ""
echo "[2/6] Installing system dependencies on Pi..."
ssh "$PI" bash <<'ENDSSH'
set -e
export DEBIAN_FRONTEND=noninteractive
sudo apt-get update -qq
sudo apt-get install -y -qq portaudio19-dev python3 python3-pip python3-venv git
# install uv if not present
if ! command -v uv &>/dev/null; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
  echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
fi
echo "    System deps OK."
ENDSSH

# ── 3. Create remote directory and sync files ─────────────────────────────────
echo ""
echo "[3/6] Syncing satellite files..."
ssh "$PI" "mkdir -p $REMOTE_DIR"
rsync -az --delete \
  "$SAT_DIR/satellite.py" \
  "$SAT_DIR/satellite.service" \
  "$PI:$REMOTE_DIR/"
echo "    Files synced."

# ── 4. Create/update Python venv ─────────────────────────────────────────────
echo ""
echo "[4/6] Setting up Python environment..."
ssh "$PI" bash <<ENDSSH
set -e
cd "$REMOTE_DIR"
if [[ ! -d venv ]]; then
  python3 -m venv venv
fi
venv/bin/pip install -q --upgrade pip
venv/bin/pip install -q \
  sounddevice \
  numpy \
  httpx \
  wyoming
echo "    Python env OK."
ENDSSH

# ── 5. Write .env on the Pi ───────────────────────────────────────────────────
echo ""
echo "[5/6] Writing .env on Pi..."
ssh "$PI" bash <<ENDSSH
cat > "$REMOTE_DIR/.env" <<'EOF'
SATELLITE_ID=$SATELLITE_ID
SERVER_HOST=$SERVER_IP
BACKEND_URL=http://$SERVER_IP:8000
BACKEND_API_KEY=$BACKEND_API_KEY
WAKE_HOST=$SERVER_IP
STT_HOST=$SERVER_IP
TTS_HOST=$SERVER_IP
STT_PORT=10300
TTS_PORT=10200
WAKE_PORT=10400
# USB mic device index — leave unset to use ALSA default (configured in /etc/asound.conf)
# Override with a number if you have multiple mics: AUDIO_DEVICE=1
# AUDIO_DEVICE=
EOF
echo "    .env written."
ENDSSH

# ── 6. Install + (re)start systemd service ────────────────────────────────────
echo ""
echo "[6/6] Installing systemd service..."
ssh "$PI" bash <<ENDSSH
set -e
# Customize the service file with the actual user
sed "s/%i/$PI_USER/g" "$REMOTE_DIR/satellite.service" \
  | sudo tee /etc/systemd/system/mauricio-satellite.service > /dev/null
sudo systemctl daemon-reload
sudo systemctl enable mauricio-satellite
sudo systemctl restart mauricio-satellite
sleep 2
STATUS=\$(sudo systemctl is-active mauricio-satellite)
echo "    Service status: \$STATUS"
if [[ "\$STATUS" != "active" ]]; then
  echo ""
  echo "Service failed to start. Check logs with:"
  echo "  ssh $PI journalctl -u mauricio-satellite -n 50"
  exit 1
fi
ENDSSH

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Satellite '$SATELLITE_ID' deployed and running."
echo ""
echo "  Useful commands:"
echo "    Logs   : ssh $PI journalctl -u mauricio-satellite -f"
echo "    Stop   : ssh $PI sudo systemctl stop mauricio-satellite"
echo "    Restart: ssh $PI sudo systemctl restart mauricio-satellite"
echo ""
echo "  The wyoming services must be running on this Mac:"
echo "    docker compose up -d whisper piper openwakeword"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
