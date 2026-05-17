#!/bin/bash
# install-mac-mini-matchdaypro.sh — installer for the matchdaypro donor
# hotline agent on Mac Mini. Sibling of install-mac-mini-autosync.sh.
#
# Both reuse the same Python venv at livekit/.venv. They're separate
# launchd jobs reading separate .env files (matchdaypro reads .env.matchdaypro).
#
# Run again any time to update.

set -euo pipefail

REPO_DIR="$HOME/portfolio-booking-agent"
LIVEKIT_DIR="$REPO_DIR/livekit"
VENV_DIR="$LIVEKIT_DIR/.venv"
PLIST_NAME="com.matchdaypro.voice-agent"
PLIST_INSTALLED="$HOME/Library/LaunchAgents/${PLIST_NAME}.plist"
ENV_FILE="$LIVEKIT_DIR/.env.matchdaypro"
LOG_DIR="$HOME/Library/Logs"

echo "=== matchdaypro donor hotline installer ==="

if [ -d "$REPO_DIR/.git" ]; then
    echo "[1/6] Updating repo at $REPO_DIR..."
    git -C "$REPO_DIR" pull --ff-only
else
    echo "[1/6] Cloning repo to $REPO_DIR..."
    git clone https://github.com/chaimgelber23/portfolio-booking-agent.git "$REPO_DIR"
fi

if [ ! -d "$VENV_DIR" ]; then
    echo "[2/6] Creating Python venv at $VENV_DIR..."
    /opt/homebrew/bin/python3 -m venv "$VENV_DIR"
fi

echo "[3/6] Installing Python deps..."
"$VENV_DIR/bin/pip" install --quiet --upgrade pip
"$VENV_DIR/bin/pip" install --quiet -r "$LIVEKIT_DIR/requirements.txt"

if [ ! -f "$ENV_FILE" ]; then
    echo "[4/6] Creating $ENV_FILE template — fill in real values before starting."
    cat > "$ENV_FILE" << 'EOF'
# Required by livekit/matchdaypro_agent.py — fill these in before launchctl load
LIVEKIT_URL=
LIVEKIT_API_KEY=
LIVEKIT_API_SECRET=
OPENAI_API_KEY=
ELEVEN_API_KEY=
DEEPGRAM_API_KEY=
MDP_VOICE_TOOL_URL=https://matchdaypro.vercel.app/api/voice/tool
MDP_VOICE_TOOL_SECRET=
MDP_DEFAULT_HOTLINE_VOICE_ID=XB0fDUnXU5powFXDhCwa
EOF
    echo "    -> edit: $ENV_FILE"
    echo "    -> re-run this script after filling it in"
    exit 0
else
    echo "[4/6] $ENV_FILE exists, skipping template"
fi

# Sanity: refuse to install plist if env is empty
if grep -E '^(LIVEKIT_API_KEY|OPENAI_API_KEY|ELEVEN_API_KEY|DEEPGRAM_API_KEY|MDP_VOICE_TOOL_SECRET)=$' "$ENV_FILE" > /dev/null; then
    echo "ERROR: $ENV_FILE has empty required vars. Fill them in first."
    exit 1
fi

mkdir -p "$LOG_DIR" "$HOME/Library/LaunchAgents"
PLIST_SRC="$LIVEKIT_DIR/${PLIST_NAME}.plist"
PYTHON_BIN="$VENV_DIR/bin/python3"

# Swap homebrew python for the venv python for dependency isolation.
sed "s|<string>/opt/homebrew/bin/python3</string>|<string>$PYTHON_BIN</string>|" "$PLIST_SRC" > "$PLIST_INSTALLED"

echo "[5/6] Installed plist at $PLIST_INSTALLED"

echo "[6/6] (Re)loading launchd job..."
launchctl unload "$PLIST_INSTALLED" 2>/dev/null || true
launchctl load "$PLIST_INSTALLED"

sleep 2
if launchctl list | grep -q "$PLIST_NAME"; then
    PID=$(launchctl list | grep "$PLIST_NAME" | awk '{print $1}')
    echo "OK Loaded — PID=$PID"
else
    echo "FAIL — check $LOG_DIR/${PLIST_NAME}.err.log"
    exit 1
fi

echo ""
echo "Logs:    tail -f $LOG_DIR/${PLIST_NAME}.log"
echo "Errors:  tail -f $LOG_DIR/${PLIST_NAME}.err.log"
echo "Stop:    launchctl unload $PLIST_INSTALLED"
echo "Start:   launchctl load $PLIST_INSTALLED"
