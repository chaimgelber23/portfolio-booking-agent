#!/usr/bin/env python3
"""Hourly synthetic dispatch smoke test for the matchdaypro voice agent.

Dispatches a fake job to LiveKit with a room named `mdp-smoke-{ts}`.
The matchdaypro_agent.py recognizes the prefix and runs a lightweight
entrypoint that exercises VAD+STT+LLM+TTS init without calling the
production tool API. This script then watches the agent log for the
`smoke OK mdp-smoke-{ts}` line within 25s. If the line never appears,
posts a failure alert to TELEGRAM_CHAT_SMOKE.

Run hourly via ~/Library/LaunchAgents/com.voice-agent.smoke-test.plist.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

from dotenv import load_dotenv
from livekit import api

ENV_PATH = Path.home() / "portfolio-booking-agent/scripts/.env.watchdog"
MDP_ENV = Path.home() / "portfolio-booking-agent/livekit/.env.matchdaypro"
LOG_PATH = Path.home() / "Library/Logs/com.matchdaypro.voice-agent.log"
TIMEOUT_SECS = 25  # how long to wait for "smoke OK"


def send_alert(text: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_SMOKE") or os.environ.get("TELEGRAM_CHAT_HEALTH")
    if not token or not chat:
        print("ERROR: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_SMOKE missing", file=sys.stderr)
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    body = json.dumps({"chat_id": chat, "text": text, "parse_mode": "HTML"}).encode()
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}
    )
    try:
        urllib.request.urlopen(req, timeout=10).read()
    except Exception as e:
        print(f"telegram send failed: {e}", file=sys.stderr)


def watch_log_for(needle: str, deadline_ts: float, start_pos: int) -> bool:
    """Tail the matchdaypro log starting at start_pos, return True if needle
    appears in any line before deadline_ts."""
    while time.time() < deadline_ts:
        try:
            with LOG_PATH.open() as f:
                f.seek(start_pos)
                for line in f:
                    if needle in line:
                        return True
        except FileNotFoundError:
            pass
        time.sleep(0.5)
    return False


async def run_smoke() -> None:
    # Load Telegram creds first (so failure alerts can fire)
    load_dotenv(ENV_PATH)
    # Then load LiveKit creds from the matchdaypro env (same project as the agent)
    load_dotenv(MDP_ENV)

    ts = int(time.time())
    room_name = f"mdp-smoke-{ts}"
    needle = f"smoke OK {room_name}"
    log_start = LOG_PATH.stat().st_size if LOG_PATH.exists() else 0

    # Dispatch a job to the matchdaypro agent. agent_name="" matches anonymous
    # workers (which is what all our agents register as).
    livekit_url = os.environ["LIVEKIT_URL"].replace("wss://", "https://")
    lk = api.LiveKitAPI(livekit_url, os.environ["LIVEKIT_API_KEY"], os.environ["LIVEKIT_API_SECRET"])

    dispatch_start = time.time()
    try:
        await lk.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(
                agent_name="",
                room=room_name,
                metadata=json.dumps({"smoke_test": True, "ts": ts}),
            )
        )
    except Exception as e:
        send_alert(
            f"🔴 <b>matchdaypro smoke FAIL</b>\n"
            f"could not create dispatch: <code>{str(e)[:300]}</code>"
        )
        await lk.aclose()
        return

    # Watch the agent log for our success line
    deadline = time.time() + TIMEOUT_SECS
    ok = await asyncio.get_event_loop().run_in_executor(
        None, watch_log_for, needle, deadline, log_start
    )
    elapsed = time.time() - dispatch_start

    if ok:
        # Optional: success log (no Telegram). Comment out the next line to
        # only see failures in Telegram. Leave it on for the first few days
        # so we know the smoke loop is actually firing.
        send_alert(f"🟢 matchdaypro smoke OK ({elapsed:.1f}s)")
    else:
        # Failure — read the last few lines of the agent log to include
        last_lines = ""
        try:
            with LOG_PATH.open() as f:
                f.seek(max(log_start, LOG_PATH.stat().st_size - 4000))
                last_lines = f.read()[-2000:]
        except Exception:
            pass
        send_alert(
            f"🔴 <b>matchdaypro smoke FAIL</b>\n"
            f"dispatched <code>{room_name}</code>, no <code>smoke OK</code> "
            f"in agent log within {TIMEOUT_SECS}s.\n\n"
            f"Last log:\n<pre>{last_lines[-1200:]}</pre>"
        )

    # Cleanup: best-effort delete the test room
    try:
        await lk.room.delete_room(api.DeleteRoomRequest(room=room_name))
    except Exception:
        pass

    await lk.aclose()


if __name__ == "__main__":
    asyncio.run(run_smoke())
