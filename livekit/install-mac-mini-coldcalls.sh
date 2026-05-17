#!/usr/bin/env bash
# Install the outbound cold-call LiveKit agent on the Mac Mini.
# Run ON the Mac Mini (or via `ssh mac-mini bash install-mac-mini-coldcalls.sh`).

set -euo pipefail

REPO_DIR="/Users/chaimgelber/portfolio-booking-agent"
LK_DIR="$REPO_DIR/livekit"
ENV_FILE="$LK_DIR/.env.coldcalls"
PLIST_SRC="$LK_DIR/com.coldcalls.voice-agent.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.coldcalls.voice-agent.plist"

echo "==> Checking repo presence at $REPO_DIR"
if [ ! -d "$LK_DIR" ]; then
  echo "ERR: $LK_DIR not found. Pull the portfolio-booking-agent repo first."
  exit 1
fi

cd "$LK_DIR"

echo "==> Installing Python deps"
/opt/homebrew/bin/python3 -m pip install --user --upgrade -r requirements.txt

echo "==> Checking .env.coldcalls"
if [ ! -f "$ENV_FILE" ]; then
  cat > "$ENV_FILE" <<'EOF'
# Required — fill these in before launching
LIVEKIT_URL=
LIVEKIT_API_KEY=
LIVEKIT_API_SECRET=
OPENAI_API_KEY=
ELEVEN_API_KEY=
ELEVEN_VOICE_ID=21m00Tcm4TlvDq8ikWAM
DEEPGRAM_API_KEY=
COLD_CALLS_TOOL_URL=https://seohandoff.com/api/cold-calls/tool
COLD_CALLS_TOOL_SECRET=
EOF
  echo "  Wrote stub $ENV_FILE — edit it before continuing."
  echo "  Required: LIVEKIT_* + OPENAI_API_KEY + ELEVEN_API_KEY + DEEPGRAM_API_KEY + COLD_CALLS_TOOL_SECRET"
  exit 2
fi

if grep -qE '^(LIVEKIT_API_KEY|OPENAI_API_KEY|ELEVEN_API_KEY|DEEPGRAM_API_KEY|COLD_CALLS_TOOL_SECRET)=$' "$ENV_FILE"; then
  echo "ERR: One or more required env vars in $ENV_FILE are empty. Fill them in and re-run."
  exit 3
fi

echo "==> Installing launchd plist"
mkdir -p "$HOME/Library/LaunchAgents"
cp "$PLIST_SRC" "$PLIST_DST"

echo "==> Unloading any previous instance (ok if it fails the first time)"
launchctl unload "$PLIST_DST" 2>/dev/null || true

echo "==> Loading agent"
launchctl load "$PLIST_DST"

sleep 2

echo "==> Status check"
launchctl list | grep com.coldcalls.voice-agent || echo "  (not in list yet — check logs)"

echo ""
echo "✅ Installed. Logs:"
echo "  tail -f $HOME/Library/Logs/com.coldcalls.voice-agent.log"
echo "  tail -f $HOME/Library/Logs/com.coldcalls.voice-agent.err.log"
