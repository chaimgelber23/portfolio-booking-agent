"""LiveKit Agents (1.5+) outbound cold-call agent for the 5-business hub.

Listens for rooms named `cc-{biz}-{callid}` created by
seo-business/src/lib/cold-calling/livekit-engine.ts placeLiveKitCall().

Reads prospect + script + business config from the SIP participant's metadata
(serialized JSON), opens the conversation, drives objection handling, books a
meeting via the cold-calls tool endpoint, detects voicemail, and reports the
outcome on call end.

Run on Mac Mini:
    python3 cold_call_agent.py dev    # local test
    python3 cold_call_agent.py start  # production (called by launchd plist)

Required env vars (loaded from .env.coldcalls via python-dotenv):
    LIVEKIT_URL              wss://<project>.livekit.cloud
    LIVEKIT_API_KEY
    LIVEKIT_API_SECRET
    OPENAI_API_KEY           gpt-4o-mini for in-call reasoning
    ELEVEN_API_KEY
    ELEVEN_VOICE_ID          21m00Tcm4TlvDq8ikWAM = Rachel (or pick another)
    DEEPGRAM_API_KEY         nova-2 STT
    COLD_CALLS_TOOL_URL      https://seohandoff.com/api/cold-calls/tool
    COLD_CALLS_TOOL_SECRET   matches CRON_SECRET / dedicated secret in Vercel
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Optional

import httpx
from dotenv import load_dotenv
from livekit.agents import (
    Agent,
    AgentSession,
    AutoSubscribe,
    JobContext,
    RunContext,
    WorkerOptions,
    cli,
    function_tool,
)
from livekit.plugins import deepgram, elevenlabs, openai, silero

load_dotenv(".env.coldcalls")
logger = logging.getLogger("cold-call-agent")
logger.setLevel(logging.INFO)


# ---------------------------------------------------------------------------
# Tool client — posts to seo-business /api/cold-calls/tool
# ---------------------------------------------------------------------------

class ColdCallToolClient:
    def __init__(self) -> None:
        self.url = os.environ["COLD_CALLS_TOOL_URL"]
        self.secret = os.environ["COLD_CALLS_TOOL_SECRET"]
        self.client = httpx.AsyncClient(timeout=15.0)

    async def call(self, intent: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = {"intent": intent, **payload}
        try:
            r = await self.client.post(
                self.url,
                headers={
                    "Authorization": f"Bearer {self.secret}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
            if r.status_code >= 400:
                logger.warning("intent %s HTTP %s: %s", intent, r.status_code, r.text[:200])
                try:
                    return r.json()
                except Exception:
                    return {"error": f"HTTP {r.status_code}", "ok": False}
            return r.json()
        except Exception as e:
            logger.exception("intent %s failed", intent)
            return {"error": str(e), "ok": False}


# ---------------------------------------------------------------------------
# Variable substitution for script templates
# ---------------------------------------------------------------------------

def fill_template(template: str, vars: dict[str, Any]) -> str:
    out = template
    for k, v in vars.items():
        out = out.replace(f"{{{{{k}}}}}", str(v) if v is not None else "")
    return out


def build_script_vars(metadata: dict[str, Any]) -> dict[str, Any]:
    issues = metadata.get("top_issues") or []
    return {
        "contact_name": (metadata.get("contact_name") or "there").split(" ")[0],
        "business_name": metadata.get("domain", "").replace("https://", "").replace("http://", "").rstrip("/"),
        "domain": metadata.get("domain") or "",
        "industry": metadata.get("industry") or "local businesses",
        "city": metadata.get("city") or "your area",
        "state": metadata.get("state") or "",
        "score": metadata.get("score") if metadata.get("score") is not None else "low",
        "top_issues": ", ".join(issues[:3]) if isinstance(issues, list) else str(issues),
        "issue_count": len(issues) if isinstance(issues, list) else 0,
    }


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

def build_system_prompt(metadata: dict[str, Any], script_vars: dict[str, Any]) -> str:
    script = metadata.get("script") or {}
    business_name = metadata.get("business_name") or "the team"
    pricing = metadata.get("pricing") or []
    pricing_lines = "\n".join(f"- {p.get('name')}: {p.get('price')}" for p in pricing) if pricing else "(not set)"

    objections = script.get("objections") or {}
    objection_lines = "\n".join(
        f"  - When they say \"{k}\": {fill_template(v, script_vars)}"
        for k, v in objections.items()
    ) if objections else "  (none configured)"

    prospect_name = script_vars.get("contact_name", "there")

    return f"""You are Phil from {business_name}, calling a small-business owner cold. \
This is a real phone call. You initiated the call. You have ~90 seconds to earn \
permission to keep talking.

WHO YOU ARE CALLING
- Contact: {script_vars.get('contact_name')} at {script_vars.get('business_name')}
- Industry: {script_vars.get('industry')} in {script_vars.get('city')}, {script_vars.get('state')}
- Their score: {script_vars.get('score')}/100
- Top issues on their site: {script_vars.get('top_issues')}

PRICING (only mention if they ask)
{pricing_lines}

HOW THE CALL FLOWS
1. OPEN with the exact script line you were given. Don't paraphrase it. After \
the opener, SHUT UP and wait for them to respond.
2. If they engage (any positive signal — "yeah", "go on", "what?", a question), \
move into the value-prop and qualify naturally.
3. If they want a meeting, use book_meeting tool. Read back the time before \
confirming.
4. If they object, handle from the menu below — but keep it human, don't read \
verbatim.
5. If they're flatly not interested, gracefully exit. Don't argue.

OBJECTION RESPONSES (use as guides, not scripts)
{objection_lines}

HARD RULES
- Keep responses SHORT — 1-3 sentences. This is a phone call, not an email.
- NEVER lie about pricing or service capabilities. If they ask something you \
don't know, say "let me email you that — what's the best email?"
- If they ask "is this an AI?" or "am I talking to a robot?" — answer HONESTLY: \
"Yes, I'm an AI assistant calling on behalf of Phil at {business_name}. Want me \
to have him call you back personally?" Never deny being AI.
- If they say "do not call" / "remove me" / "take me off your list" — IMMEDIATELY \
acknowledge and call mark_dnc. Then politely end the call. No second attempts.
- If they ask "how did you get my number?" — answer truthfully: "Your business \
is listed publicly online. I audit local businesses in {script_vars.get('city')} \
to help them rank better. Want me to remove you from my list?"
- At call end (you decide it's done, or they hang up), call log_outcome with \
the right outcome label.

TOOLS YOU HAVE
- book_meeting(slot_iso, lead_name, lead_email?): books a 15-min intro on Phil's \
calendar. ONLY call after they verbally agree to a specific time AND you have \
their name. Email is optional — phone is enough.
- mark_dnc(): immediately marks this number do-not-call. Use the second they \
ask to be removed.
- log_outcome(outcome, notes?): MUST be called before ending the call. Outcomes: \
meeting_booked, interested, callback_requested, not_interested, voicemail_left, \
do_not_call, no_answer, hung_up.

Open the call now with the exact opener you were assigned. Be warm. Be real. \
Don't sound like a script."""


# ---------------------------------------------------------------------------
# Agent class
# ---------------------------------------------------------------------------

class ColdCallAgent(Agent):
    def __init__(
        self,
        *,
        instructions: str,
        tool_client: ColdCallToolClient,
        prospect_id: str,
        business: str,
        room_name: str,
    ) -> None:
        super().__init__(instructions=instructions)
        self._tool = tool_client
        self._prospect_id = prospect_id
        self._business = business
        self._room_name = room_name
        self._outcome_logged = False

    @function_tool(
        description=(
            "Book a 15-minute intro meeting on Phil's calendar. Only call after "
            "the prospect verbally agrees to a specific time AND you have their "
            "first name. slot_iso must be ISO 8601 UTC."
        ),
    )
    async def book_meeting(
        self,
        ctx: RunContext,
        slot_iso: str,
        lead_name: str,
        lead_email: str = "",
    ) -> str:
        result = await self._tool.call(
            "book_meeting",
            {
                "prospect_id": self._prospect_id,
                "business": self._business,
                "room_name": self._room_name,
                "slot_iso": slot_iso,
                "lead_name": lead_name,
                "lead_email": lead_email or None,
            },
        )
        return json.dumps(result, ensure_ascii=False)

    @function_tool(
        description=(
            "Mark this prospect as do-not-call. Call this immediately when the "
            "prospect asks to be removed, says 'do not call', or asks not to "
            "be contacted again. Always honor this request."
        ),
    )
    async def mark_dnc(self, ctx: RunContext) -> str:
        result = await self._tool.call(
            "mark_dnc",
            {
                "prospect_id": self._prospect_id,
                "business": self._business,
                "room_name": self._room_name,
            },
        )
        self._outcome_logged = True
        return json.dumps(result, ensure_ascii=False)

    @function_tool(
        description=(
            "Log the final outcome of this call. MUST be called before ending. "
            "Outcome must be one of: meeting_booked, interested, callback_requested, "
            "not_interested, voicemail_left, do_not_call, no_answer, hung_up, error."
        ),
    )
    async def log_outcome(
        self,
        ctx: RunContext,
        outcome: str,
        notes: str = "",
    ) -> str:
        result = await self._tool.call(
            "log_outcome",
            {
                "prospect_id": self._prospect_id,
                "business": self._business,
                "room_name": self._room_name,
                "outcome": outcome,
                "notes": notes or None,
            },
        )
        self._outcome_logged = True
        return json.dumps(result, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Metadata extraction
# ---------------------------------------------------------------------------

def _metadata_from_room(ctx: JobContext) -> Optional[dict[str, Any]]:
    """The SIP participant's metadata field carries the prospect + script JSON."""
    try:
        for p in ctx.room.remote_participants.values():
            md = getattr(p, "metadata", "") or ""
            if md:
                return json.loads(md)
    except Exception:
        logger.exception("failed to parse participant metadata")
    return None


def _callid_from_room_name(name: str) -> Optional[str]:
    # cc-<biz>-<uuid>
    parts = (name or "").split("-", 2)
    if len(parts) == 3 and parts[0] == "cc":
        return parts[2]
    return None


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

async def entrypoint(ctx: JobContext) -> None:
    logger.info("connecting to room %s", ctx.room.name)
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)

    if not ctx.room.name.startswith("cc-"):
        logger.info("room %s not a cold-call room — ignoring", ctx.room.name)
        return

    tool_client = ColdCallToolClient()

    # Wait briefly for the SIP participant to appear with metadata
    metadata: Optional[dict[str, Any]] = None
    for _ in range(20):  # up to 4 seconds
        metadata = _metadata_from_room(ctx)
        if metadata:
            break
        await asyncio.sleep(0.2)

    if not metadata:
        logger.error("no participant metadata on %s — disconnecting", ctx.room.name)
        return

    business = metadata.get("business") or "seo"
    prospect_id = metadata.get("prospect_id")
    if not prospect_id:
        logger.error("metadata missing prospect_id: %s", metadata)
        return

    script_vars = build_script_vars(metadata)
    instructions = build_system_prompt(metadata, script_vars)

    agent = ColdCallAgent(
        instructions=instructions,
        tool_client=tool_client,
        prospect_id=prospect_id,
        business=business,
        room_name=ctx.room.name,
    )

    session = AgentSession(
        stt=deepgram.STT(model="nova-2"),
        llm=openai.LLM(model="gpt-4o-mini", temperature=0.7),
        tts=elevenlabs.TTS(
            voice_id=os.environ.get("ELEVEN_VOICE_ID", "21m00Tcm4TlvDq8ikWAM"),
            model="eleven_turbo_v2_5",
        ),
        vad=silero.VAD.load(),
        allow_interruptions=True,
    )

    await session.start(agent=agent, room=ctx.room)

    # Open with the script's opening line, filled in. We say it directly so
    # the LLM doesn't paraphrase the carefully tuned cold opener.
    script = metadata.get("script") or {}
    opener = fill_template(script.get("opening") or "", script_vars).strip()
    voicemail = fill_template(metadata.get("voicemail_script") or "", script_vars).strip()

    if opener:
        await session.say(opener, allow_interruptions=True)

    # Voicemail heuristic: if no human speech transcribed within 8 seconds of
    # the opener, deliver the voicemail script and end. This is intentionally
    # crude — the alternative (training a beep detector) is over-engineering.
    async def _voicemail_watchdog() -> None:
        await asyncio.sleep(8.0)
        if agent._outcome_logged:
            return
        # If we got here, either there was no response or it was unintelligible.
        # Default to leaving the voicemail script. Real humans almost always say
        # *something* within 8s — even "hello?" registers.
        try:
            heard_any = any(
                p for p in ctx.room.remote_participants.values()
                if getattr(p, "audio_level", 0) > 0
            )
        except Exception:
            heard_any = False
        if heard_any:
            return
        logger.info("voicemail heuristic fired on %s", ctx.room.name)
        if voicemail:
            await session.say(voicemail, allow_interruptions=False)
        await tool_client.call(
            "log_outcome",
            {
                "prospect_id": prospect_id,
                "business": business,
                "room_name": ctx.room.name,
                "outcome": "voicemail_left",
            },
        )
        agent._outcome_logged = True
        await asyncio.sleep(1.0)
        await ctx.room.disconnect()

    asyncio.create_task(_voicemail_watchdog())

    async def _final_log() -> None:
        if agent._outcome_logged:
            return
        try:
            await tool_client.call(
                "log_outcome",
                {
                    "prospect_id": prospect_id,
                    "business": business,
                    "room_name": ctx.room.name,
                    "outcome": "hung_up",
                    "notes": "no log_outcome call from agent",
                },
            )
        except Exception:
            logger.exception("final log_outcome failed (non-fatal)")

    ctx.add_shutdown_callback(_final_log)


if __name__ == "__main__":
    # Port 8083 — gemach (8081) + autosync (8082) reserve the lower ports.
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            port=8083,
            load_threshold=0.95,
        )
    )
