"""LiveKit Agents (1.5+) voice receptionist for AutoSync AI customers (multi-tenant).

Resolves the tenant from the inbound Telnyx number, builds a per-tenant
system prompt, and exposes booking tools that POST back to seo-business's
/api/ai-voice/tool endpoint.

Run on Mac Mini:
    python3 autosync_agent.py dev    # local test
    python3 autosync_agent.py start  # production (called by launchd plist)

Required env vars (loaded from .env.autosync via python-dotenv):
    LIVEKIT_URL              wss://<project>.livekit.cloud
    LIVEKIT_API_KEY
    LIVEKIT_API_SECRET
    OPENAI_API_KEY           gpt-4o-mini
    ELEVEN_API_KEY
    ELEVEN_VOICE_ID          21m00Tcm4TlvDq8ikWAM = Rachel
    DEEPGRAM_API_KEY         nova-2 STT
    AUTOSYNC_VOICE_TOOL_URL
    AUTOSYNC_VOICE_TOOL_SECRET
"""

from __future__ import annotations

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

# Load AutoSync-specific env from .env.autosync so this agent can run
# side-by-side with the gemach agent (which reads .env in the same dir).
load_dotenv(".env.autosync")
logger = logging.getLogger("autosync-voice")
logger.setLevel(logging.INFO)


# ---------------------------------------------------------------------------
# AutoSync tool client — wraps the /api/ai-voice/tool intent dispatcher
# ---------------------------------------------------------------------------

class AutoSyncToolClient:
    def __init__(self) -> None:
        self.url = os.environ["AUTOSYNC_VOICE_TOOL_URL"]
        self.secret = os.environ["AUTOSYNC_VOICE_TOOL_SECRET"]
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
# Per-tenant system prompt
# ---------------------------------------------------------------------------

def build_system_prompt(tenant: dict[str, Any]) -> str:
    business_name = tenant.get("businessName") or "the team"
    contact_name = tenant.get("contactName") or "the owner"
    services = tenant.get("services") or "(services not yet configured)"
    reply_tone = (tenant.get("replyTone") or "friendly").lower()
    notes = tenant.get("notesForAi") or ""
    meeting_minutes = tenant.get("meetingDurationMinutes") or 30
    voice_blocks = (tenant.get("personalization") or {}).get("voicePromptBlocks") or ""

    tone_line = {
        "friendly": "Warm and conversational. Light contractions. First-name basis.",
        "formal": "Polite and professional. No contractions.",
        "concise": "Direct and lean. Short sentences. Get to the point.",
    }.get(reply_tone, "Warm and conversational. Light contractions.")

    return f"""You are AutoSync, the AI voice receptionist for {business_name}. \
A real customer or prospect just called the business line and you picked up. \
Your job is to be the front desk: answer their question if you can, take a \
message if you can't, and book an appointment when they want one.

WHO YOU ARE
- The receptionist for {business_name}, run by {contact_name}.
- Voice/tone: {tone_line}
- Default meeting length when booking: {meeting_minutes} minutes.

WHAT THE BUSINESS DOES
{services}

{f"OWNER NOTES{chr(10)}{notes}{chr(10)}" if notes else ""}\
{f"VOICE PROFILE (how the owner actually talks){chr(10)}{voice_blocks}{chr(10)}" if voice_blocks else ""}\
HARD RULES
- Never invent pricing, hours, or services not listed above. If you genuinely \
don't know, say "let me have {contact_name} get back to you on that" and \
collect their callback info.
- When booking: ALWAYS collect (1) caller's name, (2) callback phone number \
(read it back digit-by-digit to confirm), (3) email if they offer it (don't \
demand it — phone is enough), (4) one-line reason for the appointment. \
Then read back the FULL slot (day + date + time + duration) before calling \
the book_slot tool.
- Never promise a specific time before calling check_availability. The \
calendar is the source of truth.
- Keep replies SHORT — 1-2 sentences usually. This is a phone call, not an email.
- If the caller asks something off-topic (politics, jokes, your AI nature), \
politely redirect: "Happy to help with anything {business_name}-related — \
what can I do for you today?"
- If the caller is upset or escalates, offer to take a detailed message and \
have {contact_name} call them back within a business day. Don't argue.

TOOLS YOU HAVE
- check_availability(when): returns the next 5 open slots. Call this whenever \
the caller wants to book, BEFORE proposing a time.
- book_slot(slot_start, lead_name, lead_phone, lead_email?, lead_message?): \
locks the slot and creates the calendar event. Only call AFTER the read-back \
confirmation."""


# ---------------------------------------------------------------------------
# Agent class — holds the tools the LLM can call mid-conversation
# ---------------------------------------------------------------------------

class AutoSyncReceptionist(Agent):
    def __init__(
        self,
        *,
        instructions: str,
        tool_client: AutoSyncToolClient,
        client_id: str,
    ) -> None:
        super().__init__(instructions=instructions)
        self._tool = tool_client
        self._client_id = client_id

    @function_tool(
        description=(
            "Check the next available appointment slots on the business owner's "
            "calendar. Call this whenever a caller wants to book or asks about "
            "availability. Returns up to 5 open slots in the business's timezone."
        ),
    )
    async def check_availability(
        self,
        ctx: RunContext,
        when: str = "",
    ) -> str:
        """when: optional natural-language window like 'this week' or 'tomorrow afternoon'."""
        result = await self._tool.call(
            "check_availability",
            {"client_id": self._client_id, "when": when},
        )
        return json.dumps(result, ensure_ascii=False)

    @function_tool(
        description=(
            "Lock in an appointment for the caller. Only call AFTER you have "
            "read back the full slot (date + time + duration) AND collected the "
            "caller's name and callback phone number. Email is optional. The "
            "tool will create a calendar event on the owner's calendar."
        ),
    )
    async def book_slot(
        self,
        ctx: RunContext,
        slot_start: str,
        lead_name: str,
        lead_phone: str,
        lead_email: str = "",
        lead_message: str = "",
    ) -> str:
        """slot_start: ISO 8601 UTC, from check_availability output."""
        result = await self._tool.call(
            "book_slot",
            {
                "client_id": self._client_id,
                "slot_start": slot_start,
                "lead_name": lead_name,
                "lead_phone": lead_phone,
                "lead_email": lead_email or None,
                "lead_message": lead_message or None,
            },
        )
        return json.dumps(result, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Tenant resolution from inbound SIP metadata
# ---------------------------------------------------------------------------

def _to_number_from_metadata(ctx: JobContext) -> Optional[str]:
    """Pull the dialed (Telnyx) number from SIP attributes or room name."""
    try:
        for p in ctx.room.remote_participants.values():
            attrs = getattr(p, "attributes", {}) or {}
            for key in ("sip.toUser", "sip.to_user", "sip.to", "to"):
                if attrs.get(key):
                    return str(attrs[key])
    except Exception:
        pass
    name = ctx.room.name or ""
    if name.startswith("as-"):
        parts = name.split("-")
        if len(parts) >= 2 and parts[1].startswith("+"):
            return parts[1]
    return None


# ---------------------------------------------------------------------------
# Entrypoint — runs once per inbound call
# ---------------------------------------------------------------------------

async def entrypoint(ctx: JobContext) -> None:
    logger.info("connecting to room %s", ctx.room.name)
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)

    tool_client = AutoSyncToolClient()

    to_number = _to_number_from_metadata(ctx)
    if not to_number:
        logger.error("could not resolve dialed number from room %s — disconnecting", ctx.room.name)
        return

    resolve = await tool_client.call("resolve_tenant", {"to": to_number})
    if not resolve.get("ok") or not resolve.get("tenant"):
        logger.error("tenant resolve failed for %s: %s", to_number, resolve)
        return

    tenant = resolve["tenant"]
    client_id = tenant.get("clientId")
    if not client_id:
        logger.error("tenant payload missing clientId: %s", tenant)
        return

    business_name = tenant.get("businessName") or "the team"
    logger.info("call routed to tenant %s (%s) on number %s", client_id, business_name, to_number)

    agent = AutoSyncReceptionist(
        instructions=build_system_prompt(tenant),
        tool_client=tool_client,
        client_id=client_id,
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
    await session.say(
        f"Hi, thank you for calling {business_name}. How can I help you today?",
        allow_interruptions=True,
    )

    async def _log_on_end() -> None:
        try:
            await tool_client.call(
                "log_call",
                {
                    "client_id": client_id,
                    "livekit_room_id": ctx.room.name,
                    "from_e164": None,
                    "outcome": "caller_hung_up",
                },
            )
        except Exception:
            logger.exception("log_call failed (non-fatal)")

    ctx.add_shutdown_callback(_log_on_end)


if __name__ == "__main__":
    # Port 8082 because gemach's agent (com.gelber.voice-agent) reserves 8081.
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint, port=8082))
