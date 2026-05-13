#!/usr/bin/env python3
"""Voice agent watchdog. Tails every ~/Library/Logs/com.*.voice-agent.log
file and alerts a Telegram channel when known production-impact patterns
appear: entrypoint timeouts, session.start timeouts, TTS auth failures,
tenant-resolve failures.

Built 2026-05-13 after matchdaypro hotline drops became visible only by
the human caller hearing dead air. Alerts give us 0-touch visibility.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

LOG_DIR = Path.home() / "Library/Logs"
ENV_PATH = Path.home() / "portfolio-booking-agent/scripts/.env.watchdog"

# Production-impact patterns. Anything matching one of these = caller heard
# something bad (dead air, error, or no answer).
PATTERNS = [
    re.compile(r"entrypoint did not exit in time"),
    re.compile(r"session\.start timed out"),
    re.compile(r"failed to synthesize speech"),
    re.compile(r"tenant resolve failed"),
    re.compile(r"could not resolve dialed number"),
    re.compile(r"unexpected message received from elevenlabs"),
    re.compile(r"HTTP 40[12]"),  # ElevenLabs/OpenAI auth/billing failures
]
COOLDOWN_SECS = 300  # Don't fire on same agent twice within 5 min

last_alert: dict[str, float] = {}


def load_env() -> None:
    if not ENV_PATH.exists():
        print(f"FATAL: env file missing at {ENV_PATH}", file=sys.stderr)
        sys.exit(1)
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k, v.strip('"').strip("'"))


def send_alert(text: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_HEALTH")
    if not token or not chat:
        print("FATAL: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_HEALTH missing", file=sys.stderr)
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


async def tail_file(path: Path, agent_name: str) -> None:
    """Tail a file from its current end, dispatch alerts on matching lines."""
    print(f"[watchdog] tailing {agent_name}: {path}", flush=True)
    # Re-open on rotation: outer loop handles missing-file or rotation
    while True:
        try:
            with path.open() as f:
                f.seek(0, 2)
                while True:
                    line = f.readline()
                    if not line:
                        # Check inode — handle log rotation
                        try:
                            cur_inode = path.stat().st_ino
                            f_inode = os.fstat(f.fileno()).st_ino
                            if cur_inode != f_inode:
                                break  # rotated, re-open
                        except FileNotFoundError:
                            break
                        await asyncio.sleep(0.5)
                        continue
                    for p in PATTERNS:
                        if p.search(line):
                            now = time.time()
                            if now - last_alert.get(agent_name, 0) < COOLDOWN_SECS:
                                break
                            last_alert[agent_name] = now
                            snippet = line.strip()[:600]
                            text = (
                                f"🔴 <b>voice agent: {agent_name}</b>\n\n"
                                f"<code>{snippet}</code>"
                            )
                            send_alert(text)
                            print(f"[watchdog] ALERT {agent_name}: {snippet[:120]}", flush=True)
                            break
        except FileNotFoundError:
            await asyncio.sleep(2)


async def main() -> None:
    load_env()
    files = sorted(LOG_DIR.glob("com.*.voice-agent.log"))
    if not files:
        print(f"[watchdog] no voice agent logs in {LOG_DIR}", file=sys.stderr)
        sys.exit(1)
    print(f"[watchdog] starting, watching {len(files)} files", flush=True)
    # Heartbeat send so we know the daemon launched
    send_alert(f"🟢 voice-agent-watchdog started, watching {len(files)} agents")
    tasks = []
    for f in files:
        agent_name = f.stem.replace("com.", "").replace(".voice-agent", "")
        tasks.append(asyncio.create_task(tail_file(f, agent_name)))
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
