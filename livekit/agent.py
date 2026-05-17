"""LiveKit Agents v1.x voice receptionist for Gelber Gown Gemach.

Replaces the Vapi-hosted assistant. Same system prompt + same 3 tool calls,
but runs locally on Mac Mini and connects to Telnyx SIP for the phone leg.

Tool calls hit the gemach Next.js route at /api/voice with a shared bearer
secret. Storage layer (Firestore + Google Calendar sync + SMS confirmation)
stays inside that route — agent.py is just the voice/LLM/orchestration layer.

Run on Mac Mini:
    python3 agent.py dev    # local test, console-only
    python3 agent.py start  # production (called by launchd plist)

Required env vars (in livekit/.env, loaded by python-dotenv):
    LIVEKIT_URL                wss://<project>.livekit.cloud
    LIVEKIT_API_KEY            from LiveKit Cloud dashboard
    LIVEKIT_API_SECRET         from LiveKit Cloud dashboard
    OPENAI_API_KEY             gpt-4o-mini for LLM
    ELEVEN_API_KEY             11Labs for TTS
    ELEVEN_VOICE_ID            default Rachel = 21m00Tcm4TlvDq8ikWAM
    DEEPGRAM_API_KEY           nova-2 for STT
    GEMACH_VOICE_TOOL_URL      https://gelber-gown-gemach.vercel.app/api/voice
    GEMACH_VOICE_TOOL_SECRET   shared bearer the route validates
"""

from __future__ import annotations

import json
import logging
import os

import httpx
from dotenv import load_dotenv
from livekit.agents import (
    Agent,
    AgentSession,
    JobContext,
    RoomInputOptions,
    WorkerOptions,
    cli,
    function_tool,
    RunContext,
)
from livekit.plugins import deepgram, elevenlabs, openai, silero

from system_prompt import GEMACH_SYSTEM_PROMPT

load_dotenv()
logger = logging.getLogger("gemach-voice")
logger.setLevel(logging.INFO)


# ---------- Tool implementations ----------
# Each @function_tool exposes a Python coroutine to the LLM. The LLM picks
# tool + args based on the function signature + docstring + system prompt.
# All three tools proxy to the gemach Next.js /api/voice endpoint, which
# already wraps Firestore + Google Calendar + SMS.

_TOOL_URL = os.environ.get("GEMACH_VOICE_TOOL_URL", "https://gelber-gown-gemach.vercel.app/api/voice")
_TOOL_SECRET = os.environ.get("GEMACH_VOICE_TOOL_SECRET", "")
_HTTP = httpx.AsyncClient(timeout=12.0)


async def _call_route(fn_name: str, args: dict) -> str:
    """POST {tool, args} to the gemach voice endpoint. Returns string for the LLM."""
    try:
        r = await _HTTP.post(
            _TOOL_URL,
            headers={
                "Authorization": f"Bearer {_TOOL_SECRET}",
                "Content-Type": "application/json",
            },
            json={"tool": fn_name, "args": args},
        )
        r.raise_for_status()
        return json.dumps(r.json(), ensure_ascii=False)
    except httpx.HTTPStatusError as e:
        logger.warning("tool %s HTTP %s", fn_name, e.response.status_code)
        return json.dumps({"error": f"tool {fn_name} returned {e.response.status_code}"})
    except Exception as e:
        logger.exception("tool %s failed", fn_name)
        return json.dumps({"error": str(e)})


@function_tool
async def check_availability(context: RunContext, date: str) -> str:
    """Check available appointment slots for a specific date.

    Use this when a caller asks about availability or wants to book an
    appointment. Date can be natural language like 'this wednesday',
    'next motzei shabbos', or a specific date.

    Args:
        date: The date to check availability for (Eastern Time).
              Examples: 'this wednesday', 'next motzei shabbos', 'January 15'.
    """
    return await _call_route("checkAvailability", {"date": date})


@function_tool
async def create_booking(
    context: RunContext,
    name: str,
    appointment_date: str,
    slot_time: str,
    group_size: int,
    wedding_date: str,
    phone: str,
    slot_pairing: str = "single",
) -> str:
    """Create a new appointment booking.

    Only call this AFTER you have collected ALL required information AND
    done the single recap for confirmation. All times are Eastern Time.

    For groups of 5–6 people, the caller MUST be offered two options
    before this is called: (a) the last 30-minute slot of the evening, or
    (b) two regular 15-minute slots back-to-back. Pass slot_pairing="consecutive"
    for option (b); pass slot_pairing="single" (the default) for option (a)
    or for any group of 1–4.

    Args:
        name: The caller's full name (used in the record; do NOT read back to caller).
        appointment_date: Appointment date (Eastern Time). E.g., 'this wednesday' or 'motzei shabbos'.
        slot_time: Specific time slot in Eastern Time. E.g., '7:30 PM' or '11:45 AM'.
                   For consecutive bookings, pass the EARLIER of the two slots.
        group_size: Number of people attending (1-6).
        wedding_date: The caller's wedding date.
        phone: Phone number for confirmation (the caller's number).
        slot_pairing: 'single' (default) or 'consecutive'. Only matters for groups of 5–6.
    """
    return await _call_route(
        "createBooking",
        {
            "name": name,
            "appointmentDate": appointment_date,
            "slotTime": slot_time,
            "groupSize": group_size,
            "weddingDate": wedding_date,
            "phone": phone,
            "slotPairing": slot_pairing,
        },
    )


@function_tool
async def get_business_info(context: RunContext, topic: str) -> str:
    """Get specific business information.

    Use this if you need to double-check details about a topic before
    answering the caller. Prefer the system-prompt knowledge for the
    common topics — call this tool when the caller asks something
    specific and you want to make sure you have the exact wording.

    Args:
        topic: One of: hours, location, sizes, donation, alterations,
               seamstresses, pickup, return, groupSize, parking, bring,
               children, late, accessories, photos, shipping, waitlist,
               reschedule, cancel.
    """
    return await _call_route("getBusinessInfo", {"topic": topic})


# ---------- Agent class ----------

class GemachReceptionist(Agent):
    """Voice receptionist for the gemach. System prompt locked to ET, with
    char-by-char read-back for names + emails before booking."""

    def __init__(self) -> None:
        super().__init__(
            instructions=GEMACH_SYSTEM_PROMPT,
            tools=[check_availability, create_booking, get_business_info],
        )


# ---------- Entry point ----------

async def entrypoint(ctx: JobContext) -> None:
    logger.info("connecting to room %s", ctx.room.name)
    await ctx.connect()

    session = AgentSession(
        vad=silero.VAD.load(),
        stt=deepgram.STT(model="nova-2"),
        llm=openai.LLM(model="gpt-4o-mini", temperature=0.7),
        tts=elevenlabs.TTS(
            voice_id=os.environ.get("ELEVEN_VOICE_ID", "21m00Tcm4TlvDq8ikWAM"),
            model="eleven_turbo_v2_5",
        ),
    )

    await session.start(
        room=ctx.room,
        agent=GemachReceptionist(),
        room_input_options=RoomInputOptions(),
    )

    await session.generate_reply(
        instructions=(
            "Greet the caller exactly: "
            "'Hi, this is the Gelber Gown Gemach automated system. "
            "You can ask me questions, or book or cancel an appointment. "
            "How can I help you?'"
        ),
    )


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
