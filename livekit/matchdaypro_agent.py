"""LiveKit Agents (1.5+) inbound donor hotline for matchdaypro campaigns.

Per-tenant: resolves campaign from the dialed Telnyx number, opens with a
mandatory AI-disclosure + 3 exit ramps, then routes the call through one of
take-donation / give-status / route-to-organizer / honor-DNC.

Gold-tier defaults (configurable):
  STT  : Deepgram nova-3
  LLM  : OpenAI gpt-4o
  TTS  : ElevenLabs eleven_turbo_v2_5 with per-org cloned voice + premium
         preset fallback (Charlotte XB0fDUnXU5powFXDhCwa)
  VAD  : Silero with min_silence_duration=0.5s (donors over 60 talk slowly)

Run on Mac Mini (port 8084):
    python3 matchdaypro_agent.py dev
    python3 matchdaypro_agent.py start   # production (launchd)

Required env vars (loaded from .env.matchdaypro):
    LIVEKIT_URL                       wss://<project>.livekit.cloud
    LIVEKIT_API_KEY
    LIVEKIT_API_SECRET
    OPENAI_API_KEY                    gpt-4o
    ELEVEN_API_KEY
    DEEPGRAM_API_KEY                  nova-3
    MDP_VOICE_TOOL_URL                https://matchdaypro.vercel.app/api/voice/tool
    MDP_VOICE_TOOL_SECRET
    MDP_DEFAULT_HOTLINE_VOICE_ID      ElevenLabs preset until org clones (Charlotte default)
"""

from __future__ import annotations

import json
import logging
import os
import time
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

load_dotenv(".env.matchdaypro")
logger = logging.getLogger("mdp-hotline")
logger.setLevel(logging.INFO)

DEFAULT_VOICE_ID = os.environ.get(
    "MDP_DEFAULT_HOTLINE_VOICE_ID", "XB0fDUnXU5powFXDhCwa"  # Charlotte
)


# ---------------------------------------------------------------------------
# Tool client — wraps the matchdaypro /api/voice/tool dispatcher
# ---------------------------------------------------------------------------

class MatchdayproToolClient:
    def __init__(self) -> None:
        self.url = os.environ["MDP_VOICE_TOOL_URL"]
        self.secret = os.environ["MDP_VOICE_TOOL_SECRET"]
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
# Per-tenant system prompt — disclosure + exit ramps baked in
# ---------------------------------------------------------------------------

def build_system_prompt(tenant: dict[str, Any]) -> str:
    org_name = tenant.get("orgName") or "the campaign"
    campaign_name = tenant.get("campaignName") or "the current campaign"
    contact_name = tenant.get("contactName") or "the team"
    goal_dollars = (tenant.get("goalCents") or 0) // 100
    raised_dollars = (tenant.get("raisedCents") or 0) // 100
    hours_remaining = tenant.get("hoursRemaining")
    match_mult = tenant.get("matchMultiplier")
    matcher_name = tenant.get("matcherName")
    match_pool_remaining = (tenant.get("matchPoolRemainingCents") or 0) // 100
    forward_enabled = tenant.get("forwardEnabled")
    repeat = tenant.get("repeatCaller")

    status_line = f"Currently raised ${raised_dollars:,} of ${goal_dollars:,} goal."
    if hours_remaining is not None:
        status_line += f" {hours_remaining} hours remaining."
    match_line = ""
    if match_mult and matcher_name:
        match_line = (
            f"Every dollar is matched {match_mult}x by {matcher_name}"
            + (f" — ${match_pool_remaining:,} of matching pool still available." if match_pool_remaining else " — match pool fully claimed.")
        )

    repeat_line = ""
    if repeat:
        last_amt = (repeat.get("lastDonationCents") or 0) // 100
        if last_amt > 0:
            repeat_line = (
                f"\nThis caller previously gave ${last_amt}. "
                "If natural, you may acknowledge: 'Welcome back — thank you for your last gift.' "
                "Don't pressure them to give again, but offer the option."
            )

    forward_line = (
        "You can transfer the caller to a human at the organization by calling route_to_organizer."
        if forward_enabled
        else "If the caller wants a human, take a voicemail via take_voicemail — the organizer will call back within a business day."
    )

    return f"""You are the AI donor hotline for {org_name}, taking a call about \
the {campaign_name} campaign.

MANDATORY OPENING DISCLOSURE — the FIRST thing you say, before anything else:
"Hi, this is an AI assistant calling on behalf of {org_name}. I have a quick \
update on the {campaign_name} campaign — is now a good time? If you'd rather \
talk to a person, just say 'human.' If you don't want calls like this, say \
'remove me' and I'll take you off the list right now."

You MUST say all four parts: (1) AI disclosure, (2) what org, (3) human exit \
ramp, (4) DNC exit ramp. Don't paraphrase to make it shorter. Don't skip parts.

CAMPAIGN STATUS
{status_line}
{match_line}

WHAT THE CALLER CAN DO
1. Donate by phone — collect amount (in dollars), then call take_donation. \
The donor gets a Stripe payment link via SMS while still on the call. Read \
the amount back BEFORE calling the tool: "Just to confirm — ${{amount}} to \
{campaign_name}. I'll text the secure payment link now."
2. Hear live campaign status — call check_campaign_status and read the \
result in conversation. Don't read raw JSON.
3. Talk to a person — {forward_line}
4. Get off the list — IMMEDIATELY call mark_dnc, then say: "Done. You're off \
the list for {org_name}. Have a good day." Then end the call. Don't argue, \
don't ask why, don't try to save the call.

HARD RULES
- Never invent matching math. The current match info is in the prompt — quote it.
- When taking a donation, ALWAYS read back the dollar amount before calling \
take_donation. "Just to confirm — $36 to {campaign_name}. Sound right?"
- Read phone numbers back digit-by-digit for SMS confirmations.
- Keep replies SHORT — 1-2 sentences. This is a phone call, not an email.
- If the caller asks something off-topic (politics, jokes, your AI nature), \
acknowledge once and redirect: "Happy to help with {campaign_name} — anything \
I can do for that?"
- If a caller is upset, offer a voicemail and stay calm. Don't escalate.
- Close warmly when the caller is done: "Thank you for supporting {org_name}. \
Have a wonderful day."
- Caller can be {contact_name}'s donor base — treat every voice with respect, \
especially older voices.
{repeat_line}

TOOLS
- check_campaign_status() — live goal / raised / hours / match info.
- take_donation(amount_cents, donor_name?, donor_phone?, dedication?) — \
creates Stripe payment link, returns URL.
- send_payment_link_sms(amount_cents, to_e164?) — fires the SMS.
- route_to_organizer() — returns transfer instructions or voicemail fallback.
- take_voicemail(message_text, callback_phone?, callback_name?) — store the message.
- mark_dnc(phone_e164?, reason?) — opt the caller off this org's list."""


# ---------------------------------------------------------------------------
# Agent class — tools the LLM can call mid-call
# ---------------------------------------------------------------------------

class MatchdayproHotline(Agent):
    def __init__(
        self,
        *,
        instructions: str,
        tool_client: MatchdayproToolClient,
        call_id: str,
        org_name: str,
        campaign_name: Optional[str],
        from_e164: Optional[str],
    ) -> None:
        super().__init__(instructions=instructions)
        self._tool = tool_client
        self._call_id = call_id
        self._org_name = org_name
        self._campaign_name = campaign_name
        self._from_e164 = from_e164

    @function_tool(
        description=(
            "Get live campaign status — goal, raised so far, donor count, "
            "match multiplier, hours remaining. Call this whenever a caller "
            "asks how the campaign is going."
        ),
    )
    async def check_campaign_status(self, ctx: RunContext) -> str:
        result = await self._tool.call(
            "check_campaign_status",
            {"campaign_id": _campaign_id_from_attrs(ctx) or ""},
        )
        return json.dumps(result, ensure_ascii=False)

    @function_tool(
        description=(
            "Create a Stripe payment link for the caller's donation. ONLY call "
            "after reading back the dollar amount. Returns the secure URL. "
            "amount_cents must be in cents (e.g. $36 = 3600)."
        ),
    )
    async def take_donation(
        self,
        ctx: RunContext,
        amount_cents: int,
        donor_name: str = "",
        donor_phone: str = "",
        dedication: str = "",
    ) -> str:
        result = await self._tool.call(
            "take_donation",
            {
                "call_id": self._call_id,
                "amount_cents": int(amount_cents),
                "donor_name": donor_name or None,
                "donor_phone": donor_phone or self._from_e164,
                "dedication": dedication or None,
            },
        )
        return json.dumps(result, ensure_ascii=False)

    @function_tool(
        description=(
            "Send the Stripe payment link to the caller via SMS. Only call AFTER "
            "take_donation succeeded. to_e164 defaults to the caller's number."
        ),
    )
    async def send_payment_link_sms(
        self,
        ctx: RunContext,
        amount_cents: int,
        to_e164: str = "",
    ) -> str:
        result = await self._tool.call(
            "send_payment_link_sms",
            {
                "call_id": self._call_id,
                "to_e164": to_e164 or self._from_e164,
                "amount_cents": int(amount_cents),
                "org_name": self._org_name,
                "campaign_name": self._campaign_name,
            },
        )
        return json.dumps(result, ensure_ascii=False)

    @function_tool(
        description=(
            "Route the caller to a human at the organization. Returns either "
            "{action:'transfer', to_e164:'+1...'} or {action:'voicemail'}. If "
            "voicemail, you must collect the message via take_voicemail."
        ),
    )
    async def route_to_organizer(self, ctx: RunContext) -> str:
        result = await self._tool.call("route_to_organizer", {"call_id": self._call_id})
        return json.dumps(result, ensure_ascii=False)

    @function_tool(
        description=(
            "Store a voicemail from the caller. message_text is what they "
            "want to tell the organizer. callback_phone defaults to the "
            "caller's number; collect callback_name if natural."
        ),
    )
    async def take_voicemail(
        self,
        ctx: RunContext,
        message_text: str,
        callback_phone: str = "",
        callback_name: str = "",
    ) -> str:
        result = await self._tool.call(
            "take_voicemail",
            {
                "call_id": self._call_id,
                "message_text": message_text,
                "callback_phone": callback_phone or self._from_e164,
                "callback_name": callback_name or None,
            },
        )
        return json.dumps(result, ensure_ascii=False)

    @function_tool(
        description=(
            "Opt the caller off this organization's call list IMMEDIATELY. "
            "Call this the moment they say 'remove me', 'don't call', 'stop "
            "calling', or 'take me off the list'. After this tool succeeds, "
            "say a brief goodbye and end the call. Don't argue or save the call."
        ),
    )
    async def mark_dnc(
        self,
        ctx: RunContext,
        phone_e164: str = "",
        reason: str = "caller_requested",
    ) -> str:
        result = await self._tool.call(
            "mark_dnc",
            {
                "call_id": self._call_id,
                "phone_e164": phone_e164 or self._from_e164,
                "reason": reason,
            },
        )
        return json.dumps(result, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Helpers — pull dialed number + caller ID from SIP attributes
# ---------------------------------------------------------------------------

def _to_number_from_metadata(ctx: JobContext) -> Optional[str]:
    try:
        for p in ctx.room.remote_participants.values():
            attrs = getattr(p, "attributes", {}) or {}
            for key in ("sip.toUser", "sip.to_user", "sip.to", "to"):
                if attrs.get(key):
                    return str(attrs[key])
    except Exception:
        pass
    name = ctx.room.name or ""
    if name.startswith("mdp-"):
        parts = name.split("-")
        if len(parts) >= 2 and parts[1].startswith("+"):
            return parts[1]
    return None


def _from_number_from_metadata(ctx: JobContext) -> Optional[str]:
    try:
        for p in ctx.room.remote_participants.values():
            attrs = getattr(p, "attributes", {}) or {}
            for key in ("sip.fromUser", "sip.from_user", "sip.from", "from"):
                if attrs.get(key):
                    return str(attrs[key])
    except Exception:
        pass
    return None


# Pass campaign_id through agent_metadata so check_campaign_status can read it.
# We stash it on the JobContext via a closure-like attribute set in entrypoint.
def _campaign_id_from_attrs(ctx: RunContext) -> Optional[str]:
    try:
        agent = getattr(ctx, "agent", None) or getattr(ctx, "session", None)
        return getattr(agent, "_campaign_id", None) if agent else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Entrypoint — runs once per inbound call
# ---------------------------------------------------------------------------

async def entrypoint(ctx: JobContext) -> None:
    logger.info("connecting to room %s", ctx.room.name)
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)

    tool = MatchdayproToolClient()

    to_number = _to_number_from_metadata(ctx)
    from_number = _from_number_from_metadata(ctx)
    if not to_number:
        logger.error("could not resolve dialed number from room %s", ctx.room.name)
        return

    # Step 1 — resolve tenant (and check DNC).
    resolve = await tool.call("resolve_tenant", {"to": to_number, "from": from_number})
    if not resolve.get("ok") or not resolve.get("tenant"):
        logger.error("tenant resolve failed for %s: %s", to_number, resolve)
        return

    tenant = resolve["tenant"]
    voice_id = tenant.get("elevenlabsVoiceId") or DEFAULT_VOICE_ID
    org_name = tenant.get("orgName") or "the campaign"
    campaign_id = tenant.get("campaignId")

    # Step 2 — if DNC, brief acknowledgement + hang up. No exceptions.
    if resolve.get("dnc"):
        logger.info("DNC hit for %s on org %s — playing brief msg + ending", from_number, tenant.get("orgId"))
        session = AgentSession(
            stt=deepgram.STT(model="nova-3"),
            llm=openai.LLM(model="gpt-4o-mini", temperature=0.3),
            tts=elevenlabs.TTS(voice_id=voice_id, model="eleven_turbo_v2_5"),
            vad=silero.VAD.load(min_silence_duration=0.5),
            allow_interruptions=False,
        )
        agent = Agent(instructions="Briefly acknowledge DNC and end the call.")
        await session.start(agent=agent, room=ctx.room)
        await session.say(
            f"I see we have you on our do-not-call list for {org_name}. Confirming you're still off. Have a good day.",
            allow_interruptions=False,
        )
        # Log this as dnc_blocked outcome
        call_log = await tool.call(
            "log_call_start",
            {
                "voice_number_id": tenant["voiceNumberId"],
                "org_id": tenant["orgId"],
                "campaign_id": campaign_id,
                "to_e164": to_number,
                "from_e164": from_number,
                "livekit_room_id": ctx.room.name,
            },
        )
        if call_log.get("ok"):
            await tool.call(
                "log_call_end",
                {
                    "call_id": call_log["call_id"],
                    "outcome": "dnc_blocked",
                    "voice_id_used": voice_id,
                },
            )
        return

    # Step 3 — log call start (we need the call_id for every tool call).
    call_log = await tool.call(
        "log_call_start",
        {
            "voice_number_id": tenant["voiceNumberId"],
            "org_id": tenant["orgId"],
            "campaign_id": campaign_id,
            "to_e164": to_number,
            "from_e164": from_number,
            "livekit_room_id": ctx.room.name,
        },
    )
    if not call_log.get("ok"):
        logger.error("log_call_start failed: %s", call_log)
        return
    call_id = call_log["call_id"]

    logger.info(
        "hotline call: org=%s campaign=%s from=%s voice=%s",
        tenant.get("orgName"), campaign_id, from_number, voice_id,
    )

    # Step 4 — build the agent + session.
    agent = MatchdayproHotline(
        instructions=build_system_prompt(tenant),
        tool_client=tool,
        call_id=call_id,
        org_name=org_name,
        campaign_name=tenant.get("campaignName"),
        from_e164=from_number,
    )
    # stash campaign_id so the agent's check_campaign_status tool can find it
    setattr(agent, "_campaign_id", campaign_id)

    session = AgentSession(
        stt=deepgram.STT(model="nova-3"),
        llm=openai.LLM(model="gpt-4o", temperature=0.6),
        tts=elevenlabs.TTS(voice_id=voice_id, model="eleven_turbo_v2_5"),
        vad=silero.VAD.load(min_silence_duration=0.5),
        allow_interruptions=True,
    )

    # Track transcript turns for end-of-call save.
    transcript: list[dict[str, Any]] = []

    @session.on("user_speech_committed")
    def _on_user(msg: Any) -> None:
        try:
            transcript.append({
                "role": "caller",
                "text": getattr(msg, "transcript", None) or getattr(msg, "text", "") or str(msg),
                "ts": _now_iso(),
            })
        except Exception:
            pass

    @session.on("agent_speech_committed")
    def _on_agent(msg: Any) -> None:
        try:
            transcript.append({
                "role": "agent",
                "text": getattr(msg, "transcript", None) or getattr(msg, "text", "") or str(msg),
                "ts": _now_iso(),
            })
        except Exception:
            pass

    started = time.time()
    await session.start(agent=agent, room=ctx.room)

    # The mandatory disclosure — said first regardless of LLM speed.
    disclosure = (
        f"Hi, this is an AI assistant calling on behalf of {org_name}. "
        f"I have a quick update on the {tenant.get('campaignName') or 'the current'} campaign — "
        "is now a good time? "
        "If you'd rather talk to a person, just say 'human.' "
        "If you don't want calls like this, say 'remove me' and I'll take you off the list right now."
    )
    await session.say(disclosure, allow_interruptions=True)
    transcript.append({"role": "agent", "text": disclosure, "ts": _now_iso()})

    async def _on_end() -> None:
        try:
            await tool.call(
                "log_call_end",
                {
                    "call_id": call_id,
                    "outcome": "caller_hung_up",  # webhook may overwrite if donation/dnc landed first
                    "duration_seconds": int(time.time() - started),
                    "transcript": transcript,
                    "voice_id_used": voice_id,
                    "agent_metadata": {
                        "stt": "deepgram_nova_3",
                        "llm": "openai_gpt_4o",
                        "tts": "elevenlabs_turbo_v2_5",
                    },
                },
            )
        except Exception:
            logger.exception("log_call_end failed (non-fatal)")

    ctx.add_shutdown_callback(_on_end)


def _now_iso() -> str:
    import datetime
    return datetime.datetime.utcnow().isoformat() + "Z"


if __name__ == "__main__":
    # Port 8084 — gemach 8081, autosync 8082, cold-calls 8083, matchdaypro 8084.
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            port=8084,
            load_threshold=0.95,
        )
    )
