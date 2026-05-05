#!/bin/bash
# install-mac-mini.sh — one-shot installer for the Gelber Gown Gemach voice
# agent on Mac Mini. Run from any path; uses absolute targets.
#
# What this does:
#   1. Clones / updates the portfolio-booking-agent repo to ~/portfolio-booking-agent
#   2. Creates a Python venv at ~/portfolio-booking-agent/livekit/.venv
#   3. Installs Python deps from livekit/requirements.txt
#   4. Walks Chaim through creating .env (idempotent)
#   5. Installs the launchd plist + loads it
#   6. Tails logs so Chaim can see startup
#
# Run again any time to update — clones with git pull, reinstalls deps.

set -euo pipefail

REPO_DIR="$HOME/portfolio-booking-agent"
LIVEKIT_DIR="$REPO_DIR/livekit"
VENV_DIR="$LIVEKIT_DIR/.venv"
PLIST_NAME="com.gelber.voice-agent"
PLIST_INSTALLED="$HOME/Library/LaunchAgents/${PLIST_NAME}.plist"
LOG_DIR="$HOME/Library/Logs"

echo "=== Gelber Gown Gemach voice-agent installer ==="

# --- Step 1: clone / update repo ---
if [ -d "$REPO_DIR/.git" ]; then
    echo "[1/6] Updating repo at $REPO_DIR..."
    git -C "$REPO_DIR" pull --ff-only
else
    echo "[1/6] Cloning repo to $REPO_DIR..."
    git clone https://github.com/chaimgelber23/portfolio-booking-agent.git "$REPO_DIR"
fi

# --- Step 2: Python venv ---
if [ ! -d "$VENV_DIR" ]; then
    echo "[2/6] Creating Python venv at $VENV_DIR..."
    /opt/homebrew/bin/python3 -m venv "$VENV_DIR"
fi

# --- Step 3: install deps ---
echo "[3/6] Installing Python deps..."
"$VENV_DIR/bin/pip" install --quiet --upgrade pip
"$VENV_DIR/bin/pip" install --quiet -r "$LIVEKIT_DIR/requirements.txt"

# --- Step 4: env file ---
ENV_FILE="$LIVEKIT_DIR/.env"
if [ ! -f "$ENV_FILE" ]; then
    echo "[4/6] Creating $ENV_FILE template — fill in real values before starting."
    cat > "$ENV_FILE" << 'EOF'
# Required by livekit/agent.py — fill these in before launchctl load
LIVEKIT_URL=
LIVEKIT_API_KEY=
LIVEKIT_API_SECRET=
OPENAI_API_KEY=
ELEVEN_API_KEY=
ELEVEN_VOICE_ID=21m00Tcm4TlvDq8ikWAM
DEEPGRAM_API_KEY=
GEMACH_VOICE_TOOL_URL=https://gelber-gown-gemach.vercel.app/api/voice
GEMACH_VOICE_TOOL_SECRET=
EOF
    echo "    → edit: $ENV_FILE"
    echo "    → re-run this script after filling it in"
    exit 0
else
    echo "[4/6] $ENV_FILE exists, skipping template"
fi

# Sanity: refuse to install plist if env is empty
if grep -E '^(LIVEKIT_API_KEY|OPENAI_API_KEY|ELEVEN_API_KEY|DEEPGRAM_API_KEY|GEMACH_VOICE_TOOL_SECRET)=$' "$ENV_FILE" > /dev/null; then
    echo "ERROR: $ENV_FILE has empty required vars. Fill them in first."
    exit 1
fi

# --- Step 5: rewrite plist's python path to use the venv, then install ---
mkdir -p "$LOG_DIR" "$HOME/Library/LaunchAgents"
PLIST_SRC="$LIVEKIT_DIR/${PLIST_NAME}.plist"
PYTHON_BIN="$VENV_DIR/bin/python3"

# Replace /opt/homebrew/bin/python3 with the venv's python for isolation
sed "s|<string>/opt/homebrew/bin/python3</string>|<string>$PYTHON_BIN</string>|" "$PLIST_SRC" > "$PLIST_INSTALLED"

echo "[5/6] Installed plist at $PLIST_INSTALLED"

# --- Step 6: load via launchctl ---
echo "[6/6] (Re)loading launchd job..."
launchctl unload "$PLIST_INSTALLED" 2>/dev/null || true
launchctl load "$PLIST_INSTALLED"

sleep 2
if launchctl list | grep -q "$PLIST_NAME"; then
    PID=$(launchctl list | grep "$PLIST_NAME" | awk '{print $1}')
    echo "✅ Loaded — PID=$PID"
else
    echo "❌ Failed to load. Check $LOG_DIR/${PLIST_NAME}.err.log"
    exit 1
fi

echo ""
echo "Logs:    tail -f $LOG_DIR/${PLIST_NAME}.log"
echo "Errors:  tail -f $LOG_DIR/${PLIST_NAME}.err.log"
echo "Stop:    launchctl unload $PLIST_INSTALLED"
echo "Start:   launchctl load $PLIST_INSTALLED"
