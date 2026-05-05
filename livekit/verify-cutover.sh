#!/bin/bash
# verify-cutover.sh — run on Mac Mini after the LiveKit agent is loaded.
# Smoke-tests the path that real calls will travel:
#
#   Telnyx SIP → LiveKit → agent.py (Mac Mini) → tool calls → /api/voice
#
# Skips the actual phone leg (you do that with a real phone). Verifies:
#   1. Agent process is running (launchctl)
#   2. /api/voice route is up + auth works
#   3. checkAvailability tool returns sensible data
#   4. (Optional) createBooking on a synthetic appointment + cleanup
#
# Run: bash verify-cutover.sh

set -euo pipefail

PLIST="com.gelber.voice-agent"
ENV_FILE="$HOME/portfolio-booking-agent/livekit/.env"
GEMACH_URL="https://gelber-gown-gemach.vercel.app"

# Load .env values without echoing the secret
if [ ! -f "$ENV_FILE" ]; then
    echo "❌ $ENV_FILE not found — installer hasn't been run with creds"
    exit 1
fi
set -a && source "$ENV_FILE" && set +a

echo "=== Phase 3 cutover verifier ==="

# 1) launchd job is loaded + has a running PID
if launchctl list | grep -q "$PLIST"; then
    PID=$(launchctl list | grep "$PLIST" | awk '{print $1}')
    if [ "$PID" = "-" ] || [ -z "$PID" ]; then
        echo "❌ launchd job loaded but no PID — agent crashed at startup"
        echo "   tail $HOME/Library/Logs/${PLIST}.err.log"
        exit 1
    fi
    echo "✅ Agent running (PID=$PID)"
else
    echo "❌ launchd job not loaded — bash install-mac-mini.sh"
    exit 1
fi

# 2) /api/voice GET (health) — public route, no auth
HEALTH=$(curl -sS "$GEMACH_URL/api/voice")
if echo "$HEALTH" | grep -q '"status":"ok"'; then
    echo "✅ /api/voice health green"
else
    echo "❌ /api/voice health unexpected: $HEALTH"
    exit 1
fi

# 3) /api/voice POST with LiveKit shape — checkAvailability for next Wednesday
RESP=$(curl -sS -X POST "$GEMACH_URL/api/voice" \
    -H "Authorization: Bearer $GEMACH_VOICE_TOOL_SECRET" \
    -H "Content-Type: application/json" \
    -d '{"tool":"checkAvailability","args":{"date":"next wednesday"}}')
if echo "$RESP" | grep -q '"success":true'; then
    echo "✅ checkAvailability returned data"
    echo "   sample: $(echo "$RESP" | head -c 200)"
elif echo "$RESP" | grep -q '"available":false'; then
    echo "✅ checkAvailability returned (date blocked, that's still a valid response)"
else
    echo "❌ checkAvailability unexpected: $RESP"
    exit 1
fi

# 4) Auth refusal
RESP_NOAUTH=$(curl -sS -X POST "$GEMACH_URL/api/voice" \
    -H "Authorization: Bearer wrong-secret" \
    -H "Content-Type: application/json" \
    -d '{"tool":"getBusinessInfo","args":{"topic":"hours"}}')
if echo "$RESP_NOAUTH" | grep -q "Unauthorized"; then
    echo "✅ Bad bearer rejected (auth working)"
else
    echo "❌ Bad bearer accepted — security hole"
    exit 1
fi

echo ""
echo "All 4 cutover checks PASS. Real-call test next:"
echo "  1. From a different phone, call the Telnyx test number"
echo "  2. Walk through a full booking flow"
echo "  3. Verify booking lands in Firestore + Google Calendar event created"
echo "  4. Run gemach: node scripts/verify-reminder-cron.mjs (storage layer untouched)"
