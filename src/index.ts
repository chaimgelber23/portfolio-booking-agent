// @portfolio/booking-agent v0.1.0 — public surface area.
// Pure library: types + date utilities + booking rules + reminder scheduler +
// message parser + conversation-state helpers + Hebrew calendar wrapper.
// Storage adapters live in v0.2.0 (BookingStore interface is exposed here).
//
// Importers can use either the barrel ("@portfolio/booking-agent") for the
// most-common API, or deep-import for tree-shaking:
//   import { makeETDate } from '@portfolio/booking-agent/core/date-utils'
//   import type { BookingStore } from '@portfolio/booking-agent/adapters/storage'

// Types
export type {
    Booking,
    BookingStatus,
    ConversationState,
    Customer,
    ReminderKind,
    SmsLog,
} from './core/types';

export type { BookingStore, CreateBookingInput } from './adapters/storage/types';

// Date utilities (DST-safe ET helpers + relative-date parser)
export {
    toEasternDate,
    getETOffsetMinutes,
    makeETDate,
    easternYMD,
    shiftEasternDays,
    parseDate,
    parseTime,
    isValidAppointmentTime,
    formatDate,
    formatDateShort,
    getNextAvailableDates,
    getWeekRange,
    getNextWeekRange,
} from './core/date-utils';

// Reminder scheduler (per-booking due-window logic)
export {
    sameDayTargetTime,
    dayBeforeTargetTime,
    isInFireWindow,
    DEFAULT_GEMACH_POLICY,
    type SendTimePolicy,
    type FireWindowResult,
} from './core/reminder-scheduler';

// Booking rules (pure validation, no I/O)
export {
    APPOINTMENT_SLOTS,
    isAppointmentDay,
    validateGroupSizeForSlot,
    validateBookingDates,
    slotDurationForGroup,
    freshBookingDefaults,
} from './core/booking-rules';

// Conversation state (pure transitions; storage I/O lives in adapters)
export {
    nextConversationState,
    isBookingComplete,
    isStateExpired,
    CONVERSATION_TIMEOUT_MS,
} from './core/conversation-state';

// Message parser (NLU via OpenAI; consumer provides OPENAI_API_KEY env)
export {
    parseMessage,
    mergeData,
    getMissingFields,
    type ParsedMessage,
} from './core/message-parser';

// Hebrew calendar / Shabbos guard
export {
    isJewishHoliday,
    getBlockedDatesInRange,
    getUpcomingHolidays,
    type HolidayBlockInfo,
} from './shabbos';
