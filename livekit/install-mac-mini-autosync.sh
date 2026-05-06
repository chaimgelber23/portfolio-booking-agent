#!/bin/bash
# install-mac-mini-autosync.sh — installer for the AutoSync AI multi-tenant
# voice agent on Mac Mini. Sibling of install-mac-mini.sh (gemach single-
# tenant). Both can run side-by-side; they're separate launchd jobs reading
# separate .env files (gemach reads .env, autosync reads .env.autosync).
#
# Run again any time to update.

set -euo pipefail

REPO_DIR="$HOME/portfolio-booking-agent"
LIVEKIT_DIR="$REPO_DIR/livekit"
VENV_DIR="$LIVEKIT_DIR/.venv"
PLIST_NAME="com.autosync.voice-agent"
PLIST_INSTALLED="$HOME/Library/LaunchAgents/${PLIST_NAME}.plist"
ENV_FILE="$LIVEKIT_DIR/.env.autosync"
LOG_DIR="$HOME/Library/Logs"

echo "=== AutoSync AI voice-agent installer ==="

# --- Step 1: clone / update repo ---
if [ -d "$REPO_DIR/.git" ]; then
    echo "[1/6] Updating repo at $REPO_DIR..."
    git -C "$REPO_DIR" pull --ff-only
else
    echo "[1/6] Cloning repo to $REPO_DIR..."
    git clone https://github.com/chaimgelber23/portfolio-booking-agent.git "$REPO_DIR"
fi

# --- Step 2: Python venv (shared with gemach agent) ---
if [ ! -d "$VENV_DIR" ]; then
    echo "[2/6] Creating Python venv at $VENV_DIR..."
    /opt/homebrew/bin/python3 -m venv "$VENV_DIR"
fi

# --- Step 3: install deps ---
echo "[3/6] Installing Python deps..."
"$VENV_DIR/bin/pip" install --quiet --upgrade pip
"$VENV_DIR/bin/pip" install --quiet -r "$LIVEKIT_DIR/requirements.txt"

# --- Step 4: env file ---
if [ ! -f "$ENV_FILE" ]; then
    echo "[4/6] Creating $ENV_FILE template — fill in real values before starting."
    cat > "$ENV_FILE" << 'EOF'
# Required by livekit/autosync_agent.py — fill these in before launchctl load
LIVEKIT_URL=
LIVEKIT_API_KEY=
LIVEKIT_API_SECRET=
OPENAI_API_KEY=
ELEVEN_API_KEY=
ELEVEN_VOICE_ID=21m00Tcm4TlvDq8ikWAM
DEEPGRAM_API_KEY=
AUTOSYNC_VOICE_TOOL_URL=https://seohandoff.com/api/ai-voice/tool
AUTOSYNC_VOICE_TOOL_SECRET=
EOF
    echo "    -> edit: $ENV_FILE"
    echo "    -> re-run this script after filling it in"
    exit 0
else
    echo "[4/6] $ENV_FILE exists, skipping template"
fi

# Sanity: refuse to install plist if env is empty
if grep -E '^(LIVEKIT_API_KEY|OPENAI_API_KEY|ELEVEN_API_KEY|DEEPGRAM_API_KEY|AUTOSYNC_VOICE_TOOL_SECRET)=$' "$ENV_FILE" > /dev/null; then
    echo "ERROR: $ENV_FILE has empty required vars. Fill them in first."
    exit 1
fi

# --- Step 5: install plist (rewriting python path to use the venv) ---
mkdir -p "$LOG_DIR" "$HOME/Library/LaunchAgents"
PLIST_SRC="$LIVEKIT_DIR/${PLIST_NAME}.plist"
PYTHON_BIN="$VENV_DIR/bin/python3"

# Replace /opt/homebrew/bin/python3 with the venv's python for isolation.
# WorkingDirectory in the plist is already $LIVEKIT_DIR, so autosync_agent.py's
# load_dotenv(".env.autosync") finds the env file via cwd.
sed "s|<string>/opt/homebrew/bin/python3</string>|<string>$PYTHON_BIN</string>|" "$PLIST_SRC" > "$PLIST_INSTALLED"

echo "[5/6] Installed plist at $PLIST_INSTALLED"

# --- Step 6: load via launchctl ---
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
