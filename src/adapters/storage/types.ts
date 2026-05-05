// Storage abstraction.
// Concrete adapters live in ./firestore.ts (gemach) and ./supabase.ts (future seo-business consumers).
// The cron/webhook routes only see this interface — they never touch Firestore or Postgres directly.

import type { Booking, ConversationState, ReminderKind, SmsLog } from '../../core/types.js';

export interface CreateBookingInput {
    customerPhone: string;
    customerName: string;
    appointmentDate: Date;
    slotTime: string;
    groupSize: number;
    weddingDate: Date;
    slotDuration?: number;  // defaults set by adapter
}

export interface BookingStore {
    // --- Bookings ---
    getById(id: string): Promise<Booking | null>;
    getActiveByPhone(phone: string): Promise<Booking | null>;
    getForDate(date: Date): Promise<Booking[]>;
    getInRange(startDate: Date, endDate: Date): Promise<Booking[]>;

    /**
     * Find bookings whose target send-time for a given reminder kind falls inside
     * the [now − preMs, now + postMs] window AND whose corresponding *Sent flag is false.
     * Adapter must atomically scope by status='confirmed' and not-yet-sent.
     */
    getNeedingReminder(
        kind: Exclude<ReminderKind, 'confirmation'>,
        now: Date,
        opts: { preMs: number; postMs: number },
    ): Promise<Booking[]>;

    /**
     * Create a booking inside a slot-availability transaction. Throws 'Slot is not available'
     * if the slot is already taken at write time (race-safe).
     */
    createBooking(input: CreateBookingInput): Promise<Booking>;

    isSlotAvailable(date: Date, slotTime: string): Promise<boolean>;

    reschedule(id: string, newDate: Date, newSlotTime: string): Promise<Booking | null>;
    cancel(id: string): Promise<Booking | null>;
    markReminderSent(id: string, kind: ReminderKind): Promise<void>;

    // --- Conversation state (multi-turn SMS) ---
    getConversationState(phone: string): Promise<ConversationState | null>;
    setConversationState(phone: string, state: ConversationState): Promise<void>;
    clearConversationState(phone: string): Promise<void>;

    // --- SMS logs (audit / debugging) ---
    logSmsMessage(log: Omit<SmsLog, 'id' | 'createdAt'> & { createdAt?: Date }): Promise<void>;
}
