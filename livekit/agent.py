"""LiveKit Agents voice receptionist for Gelber Gown Gemach.

Replaces the Vapi-hosted assistant. Same system prompt + same 3 tool calls,
but runs locally on Mac Mini and connects to Telnyx SIP for the phone leg.

Tool calls hit the existing gemach Next.js route at /api/voice (which is
the renamed /api/vapi route — accepts both shapes during cutover).

Run on Mac Mini:
    python3 agent.py dev   # local test
    python3 agent.py start # production (called by launchd plist)

Required env vars:
    LIVEKIT_URL              wss://<project>.livekit.cloud
    LIVEKIT_API_KEY          from LiveKit Cloud dashboard
    LIVEKIT_API_SECRET       from LiveKit Cloud dashboard
    OPENAI_API_KEY           for LLM (gpt-4o-mini parity with Vapi today)
    ELEVEN_API_KEY           for 11Labs voice
    ELEVEN_VOICE_ID          11Labs voice — Rachel = 21m00Tcm4TlvDq8ikWAM (current Vapi voice)
    DEEPGRAM_API_KEY         for STT (nova-2 parity with Vapi today)
    GEMACH_VOICE_TOOL_URL    https://gelber-gown-gemach.vercel.app/api/voice
    GEMACH_VOICE_TOOL_SECRET shared bearer the route validates

Dependencies (requirements.txt):
    livekit-agents
    livekit-plugins-openai
    livekit-plugins-elevenlabs
    livekit-plugins-deepgram
    httpx
    python-dotenv
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx
from dotenv import load_dotenv
from livekit.agents import (
    AutoSubscribe,
    JobContext,
    WorkerOptions,
    cli,
    llm,
)
from livekit.agents.voice_assistant import VoiceAssistant
from livekit.plugins import deepgram, elevenlabs, openai, silero

from system_prompt import GEMACH_SYSTEM_PROMPT

load_dotenv()
logger = logging.getLogger("gemach-voice")
logger.setLevel(logging.INFO)


# ---------- Tool definitions (port of VAPI_TOOLS) ----------
# These mirror the 3 Vapi function-tools 1:1. The actual implementation
# proxies to the gemach Next.js /api/voice endpoint, which already has the
# Firestore booking transactions and Google Calendar sync wired up.

class GemachTools(llm.FunctionContext):
    """Function tools the LLM can call mid-conversation."""

    def __init__(self) -> None:
        super().__init__()
        self._tool_url = os.environ["GEMACH_VOICE_TOOL_URL"]
        self._tool_secret = os.environ["GEMACH_VOICE_TOOL_SECRET"]
        self._client = httpx.AsyncClient(timeout=12.0)

    async def _call_route(self, fn_name: str, args: dict[str, Any]) -> str:
        """POST to /api/voice with {tool: fn_name, args}. Returns string for the LLM."""
        try:
            r = await self._client.post(
                self._tool_url,
                headers={
                    "Authorization": f"Bearer {self._tool_secret}",
                    "Content-Type": "application/json",
                },
                json={"tool": fn_name, "args": args},
            )
            r.raise_for_status()
            data = r.json()
            return json.dumps(data, ensure_ascii=False)
        except httpx.HTTPStatusError as e:
            logger.warning("tool %s HTTP %s", fn_name, e.response.status_code)
            return json.dumps({"error": f"tool {fn_name} returned {e.response.status_code}"})
        except Exception as e:
            logger.exception("tool %s failed", fn_name)
            return json.dumps({"error": str(e)})

    @llm.ai_callable(
        description=(
            "Check available appointment slots for a specific date. "
            "Use this when a caller asks about availability or wants to book an appointment. "
            "Date can be natural language like 'this wednesday', 'next motzei shabbos', or a specific date."
        ),
    )
    async def check_availability(
        self,
        date: llm.TypeInfo(description="The date to check availability for (Eastern Time). Examples: 'this wednesday', 'next motzei shabbos', 'January 15'"),  # type: ignore[valid-type]
    ) -> str:
        return await self._call_route("checkAvailability", {"date": date})

    @llm.ai_callable(
        description=(
            "Create a new appointment booking. "
            "Only use this AFTER you have collected ALL required information AND read it back to the caller for confirmation: "
            "name, appointmentDate, slotTime, groupSize, weddingDate, phone. All times Eastern."
        ),
    )
    async def create_booking(
        self,
        name: llm.TypeInfo(description="The caller's full name"),  # type: ignore[valid-type]
        appointment_date: llm.TypeInfo(description="The appointment date (Eastern Time). Examples: 'this wednesday', 'January 15'"),  # type: ignore[valid-type]
        slot_time: llm.TypeInfo(description="The specific time slot in Eastern Time. Examples: '7:30 PM', '11:45 AM'"),  # type: ignore[valid-type]
        group_size: llm.TypeInfo(description="Number of people attending (1-6)"),  # type: ignore[valid-type]
        wedding_date: llm.TypeInfo(description="The caller's wedding date"),  # type: ignore[valid-type]
        phone: llm.TypeInfo(description="Phone number for confirmation (the caller's number)"),  # type: ignore[valid-type]
    ) -> str:
        return await self._call_route(
            "createBooking",
            {
                "name": name,
                "appointmentDate": appointment_date,
                "slotTime": slot_time,
                "groupSize": group_size,
                "weddingDate": wedding_date,
                "phone": phone,
            },
        )

    @llm.ai_callable(
        description=(
            "Get specific business information. "
            "Use this if you need to double-check details about hours, location, sizes, donation, alterations, "
            "seamstresses, pickup, return, or group size policy."
        ),
    )
    async def get_business_info(
        self,
        topic: llm.TypeInfo(description="The topic: hours, location, sizes, donation, alterations, seamstresses, pickup, return, groupSize"),  # type: ignore[valid-type]
    ) -> str:
        return await self._call_route("getBusinessInfo", {"topic": topic})


# ---------- Entry point ----------

async def entrypoint(ctx: JobContext) -> None:
    logger.info("connecting to room %s", ctx.room.name)
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)

    initial_ctx = llm.ChatContext().append(role="system", text=GEMACH_SYSTEM_PROMPT)

    voice = elevenlabs.TTS(
        api_key=os.environ["ELEVEN_API_KEY"],
        voice=elevenlabs.Voice(
            id=os.environ.get("ELEVEN_VOICE_ID", "21m00Tcm4TlvDq8ikWAM"),  # Rachel
            name="rachel",
            category="premade",
        ),
        model="eleven_turbo_v2_5",
    )

    assistant = VoiceAssistant(
        vad=silero.VAD.load(),
        stt=deepgram.STT(model="nova-2"),
        llm=openai.LLM(model="gpt-4o-mini", temperature=0.7),
        tts=voice,
        chat_ctx=initial_ctx,
        fnc_ctx=GemachTools(),
        allow_interruptions=True,
        interrupt_speech_duration=0.5,
    )

    assistant.start(ctx.room)
    await assistant.say(
        "Hi, thank you for calling Gelber Gown Gemach! Mazel tov if you're calling about a wedding. How can I help you today?",
        allow_interruptions=True,
    )


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
