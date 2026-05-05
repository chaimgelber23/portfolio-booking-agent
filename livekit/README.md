# LiveKit voice receptionist (Phase 3)

Replaces the Vapi-hosted assistant for **Gelber Gown Gemach** with a self-hosted LiveKit Agents Python worker on Mac Mini. Same conversation behavior, ~$20–50/mo flat instead of per-minute Vapi billing.

Stack:
- **LiveKit Agents** (Python framework) — orchestration
- **OpenAI gpt-4o-mini** — LLM (parity with current Vapi setup)
- **11Labs Rachel voice** — TTS (parity)
- **Deepgram nova-2** — STT (parity)
- **Telnyx SIP trunk** — phone leg
- **Mac Mini launchd** — process supervisor + auto-restart

## Files

| File | Role |
|---|---|
| `agent.py` | LiveKit Agents entry point — wires VAD/STT/LLM/TTS/tools |
| `system_prompt.py` | Gemach receptionist prompt — ported from Vapi config + TZ-locked + read-back-hardened |
| `requirements.txt` | Python deps |
| `com.gelber.voice-agent.plist` | macOS launchd config (auto-restart on crash, logs to `~/Library/Logs/`) |
| `install-mac-mini.sh` | One-shot installer Chaim runs on Mac Mini |

## Mac Mini install (~10 min once accounts are ready)

```bash
ssh mac-mini   # alias is already set up per memory
curl -fsSL https://raw.githubusercontent.com/chaimgelber23/portfolio-booking-agent/main/livekit/install-mac-mini.sh | bash
# → first run creates ~/portfolio-booking-agent/livekit/.env template, exits
# Fill in the .env values, then re-run:
bash ~/portfolio-booking-agent/livekit/install-mac-mini.sh
# → installs plist, launchctl loads, tails logs
```

The installer is idempotent. Re-run after `git pull` to update.

## Telnyx + LiveKit setup runbook (the parts only Chaim does)

These are real-money / real-account decisions and accept-LOA forms — automation can't do them. ~30 min total spread over 5–10 business days for the porting wait.

### 1. LiveKit Cloud account (~5 min, free tier covers gemach volume)

1. Sign up at https://cloud.livekit.io
2. Create a new project → name it "gelber-gown-gemach"
3. Settings → API Keys → copy `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` into the Mac Mini `.env`

### 2. Telnyx SIP trunk + test number (~10 min)

1. Sign up at https://telnyx.com (no credit card needed for test number)
2. **Buy a temporary US number** — `Numbers → Search & Buy → Local → New York 718 area`. ~$1/mo. Use this for QA before porting the real number.
3. **Create a SIP Connection** — `Voice → SIP Connections → Add SIP Connection`
   - Name: "gelber-gown-gemach-livekit"
   - Connection type: "FQDN"
   - FQDN: paste the LiveKit project's SIP URI (LiveKit dashboard → Settings → SIP)
4. **Assign the test number to the SIP connection**
5. Test by calling the test number — should ring through to LiveKit (will fail until agent.py is running on Mac Mini)

### 3. QA on test number (~30 min, do BEFORE porting 347-507-5981)

Once Mac Mini agent is running:
- Call the Telnyx test number from a different phone
- Walk through a full booking flow (Wednesday + Motzei Shabbos scenarios)
- Verify: reads back name char-by-char, locks all times to Eastern, books to Firestore, syncs to Google Calendar
- Run gemach's existing `node scripts/verify-reminder-cron.mjs` to confirm storage layer untouched

### 4. Initiate Telnyx port-out of 347-507-5981 (5–10 business days)

This is the long pole — Telnyx files an LOA (Letter of Authorization) with the current carrier (Vapi's underlying carrier, likely Twilio). Carrier reviews + accepts.

1. Telnyx dashboard → `Numbers → Port Numbers → Start a Port`
2. Source: 347-507-5981, current carrier: Vapi (Twilio underneath)
3. Upload signed LOA (Telnyx generates a PDF, Chaim signs)
4. Telnyx submits to Twilio → Twilio reviews → port completes (5–10 business days, sometimes longer over Yom Tov)
5. **DURING the port window:** Both numbers stay live. Old number → Vapi → old assistant. Test number → LiveKit → new agent.

### 5. Cutover day (when port completes — Telnyx emails)

When Telnyx confirms the port:
1. The number 347-507-5981 now routes to Telnyx instead of Twilio.
2. Assign 347-507-5981 to the same SIP connection used by the test number.
3. Call 347-507-5981 from a different phone. Verify LiveKit answers, full booking flow works.
4. Decommission Vapi: pause the assistant, stop billing.
5. Optional: release the Telnyx test number to avoid the $1/mo charge.

## Verification (after cutover)

- Real call to 347-507-5981 → Mac Mini answers, agent works end-to-end
- `kill <pid_of_python>` on Mac Mini → launchd auto-restarts within 10s
- gemach's `scripts/verify-reminder-cron.mjs` still passes (storage layer untouched)
- Vapi monthly invoice goes to $0 the next billing cycle

## Cost target

| Component | Cost |
|---|---|
| LiveKit Cloud (free tier) | $0 |
| Mac Mini (already owned) | $0 |
| Telnyx SIP — phone number | ~$1/mo |
| Telnyx SIP — voice minutes (inbound) | ~$0.0035/min |
| OpenAI gpt-4o-mini | per-call: pennies |
| 11Labs voice | per-call: pennies for Rachel turbo_v2_5 |
| Deepgram nova-2 STT | per-call: pennies |

At gemach call volume (probably <50 calls/mo, ~3 min each) total monthly cost is **under $10**. Vapi's per-minute platform fee alone was running $0.10–0.15/min — same volume = $20–25/mo.

## Why not just keep Vapi

Vapi works fine for the gemach today. The migration only makes sense because:
1. Cold-calling project (planned, 4 businesses) needs voice infra and Vapi billing scales linearly with call volume — outbound cold-calling is high-volume.
2. Future SaaS "voice receptionist" SKU on AutoSync AI needs a unit-economics-friendly stack to resell.
3. All three use cases share the same LiveKit + Telnyx + Mac Mini setup. Pay the install cost once, amortize across portfolio.

If only the gemach were on the table, Phase 3 would be skip-it.
