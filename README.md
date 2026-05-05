# @portfolio/booking-agent

Reusable booking + SMS infrastructure shared across the portfolio. v0.1.0 ships as a **pure library** — types, date utilities (DST-safe Eastern Time helpers), reminder due-window scheduler, booking rules, message parser (OpenAI), conversation-state transitions, Hebrew calendar / Shabbos guard. Storage adapters and voice adapters land in v0.2.0+.

## Install

```json
{
  "dependencies": {
    "@portfolio/booking-agent": "github:chaimgelber23/portfolio-booking-agent#main"
  }
}
```

In `next.config.ts`:

```ts
const nextConfig: NextConfig = {
  transpilePackages: ['@portfolio/booking-agent'],
  // ...
};
```

The package ships TypeScript source — Next.js compiles it via `transpilePackages`. No build step in the package itself.

## Usage

```ts
// Date helpers (DST-safe Eastern Time)
import { makeETDate, parseDate, formatDate } from '@portfolio/booking-agent/core/date-utils';

const wedAt8am = makeETDate(2026, 5, 6, 8, 30);  // → exact UTC instant for 8:30 AM EDT
const parsed = parseDate('next motzei shabbos');

// Reminder due-window scheduler — the pattern that fixes "cron fired after appointment"
import { sameDayTargetTime, isInFireWindow } from '@portfolio/booking-agent/core/reminder-scheduler';

const target = sameDayTargetTime(booking.appointmentDate);
const window = isInFireWindow(target, new Date(), 15 * 60_000, 30 * 60_000);
if (window.fire) await sendSms(...);

// Pure booking validation
import {
  validateGroupSizeForSlot,
  validateBookingDates,
  freshBookingDefaults,
} from '@portfolio/booking-agent/core/booking-rules';

const err = validateGroupSizeForSlot(5, '11:30 AM', new Date('2026-05-06')); // null = OK

// Hebrew calendar
import { isJewishHoliday } from '@portfolio/booking-agent/shabbos';

const block = isJewishHoliday(new Date('2026-09-22'));
if (block.blocked) console.log('blocked:', block.reason);
```

## Storage seam (BookingStore interface)

Concrete adapters land in v0.2.0. The interface is exposed now so route code can be written against it.

```ts
import type { BookingStore } from '@portfolio/booking-agent/adapters/storage';

async function dueWindowCron(store: BookingStore, now = new Date()) {
  const due = await store.getNeedingReminder('sameDay', now, {
    preMs: 15 * 60_000,
    postMs: 30 * 60_000,
  });
  for (const b of due) {
    await sendSms(b.customerPhone, '...');
    await store.markReminderSent(b.id, 'sameDay');
  }
}
```

## What's in v0.1.0

- Types: `Booking`, `BookingStatus`, `Customer`, `ConversationState`, `ReminderKind`, `SmsLog`
- `core/date-utils` — `toEasternDate`, `makeETDate`, `easternYMD`, `shiftEasternDays`, `parseDate`, `formatDate`, `getNextAvailableDates`, `getWeekRange`, `getETOffsetMinutes`
- `core/reminder-scheduler` — `sameDayTargetTime`, `dayBeforeTargetTime`, `isInFireWindow`, `DEFAULT_GEMACH_POLICY`
- `core/booking-rules` — `APPOINTMENT_SLOTS`, `isAppointmentDay`, `validateGroupSizeForSlot`, `validateBookingDates`, `slotDurationForGroup`, `freshBookingDefaults`
- `core/conversation-state` — `nextConversationState`, `isBookingComplete`, `isStateExpired`, `CONVERSATION_TIMEOUT_MS`
- `core/message-parser` — `parseMessage`, `mergeData`, `getMissingFields` (uses `OPENAI_API_KEY` from env)
- `shabbos` — `isJewishHoliday`, `getBlockedDatesInRange`, `getUpcomingHolidays`
- `adapters/storage` — `BookingStore` interface + `CreateBookingInput`

## What's NOT in v0.1.0

- Firestore / Supabase BookingStore implementations (consumers wire their own; gemach does this in `src/lib/firebase-admin.ts` + `src/lib/sms/booking-handler.ts`)
- Twilio sender wrapper (consumer provides — every consumer has different routing/from-number rules)
- Google Calendar sync adapter (gemach-specific format; will generalize in v0.2.0)
- Voice agent adapter (Vapi today, LiveKit Agents on Mac Mini in Phase 3)

## Consumers

- **Gelber Gown Gemach** — `gelber-gown-gemach.vercel.app`
- **Cold-calling project** — planned
- **Future SaaS voice receptionist SKU** — planned

## History

Originally lived inside `seo-business/packages/booking-agent/` as a workspace member. Split out 2026-05-05 into its own repo because npm doesn't natively support git-URL path-subset syntax — `github:owner/repo#main&path:packages/x` silently clones the whole repo into `node_modules` instead of just the subpath. Standalone repo solves this cleanly.
