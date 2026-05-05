// Core domain types for the booking-agent.
// Storage-agnostic — the BookingStore interface in ../adapters/storage/types.ts
// translates to/from concrete Firestore Timestamp / Postgres timestamptz / etc.

export type BookingStatus = 'pending' | 'confirmed' | 'completed' | 'cancelled' | 'no-show';

export type ReminderKind = 'confirmation' | 'dayBefore' | 'sameDay' | 'return';

export interface Customer {
    id: string;
    name: string;
    phone: string;
    createdAt: Date;
    updatedAt: Date;
}

export interface Booking {
    id: string;
    customerId: string;
    customerName: string;
    customerPhone: string;

    // Appointment details
    appointmentDate: Date;
    slotTime: string;
    slotDuration: number;
    groupSize: number;

    // Wedding details (gemach-specific but kept generic — repurpose for "event date" elsewhere)
    weddingDate: Date;

    // Status
    status: BookingStatus;

    // Item tracking (gemach: gown lifecycle. For other businesses, repurpose or ignore.)
    gownSelected?: boolean;
    gownDescription?: string;
    gownPickedUp?: boolean;
    gownPickupDate?: Date;
    gownReturned?: boolean;
    gownReturnDate?: Date;
    donationAmount?: number;
    donationPaid?: boolean;

    // Reminder tracking
    confirmationSent: boolean;
    dayBeforeReminderSent: boolean;
    sameDayReminderSent?: boolean;
    returnReminderSent?: boolean;

    // Metadata
    createdAt: Date;
    updatedAt: Date;
    notes?: string;
}

/**
 * Conversation state used by the pure helpers (parseMessage, mergeData,
 * nextConversationState, isBookingComplete). The date fields are typed as
 * `unknown` so consumers can store Firestore Timestamps, Postgres timestamptz,
 * or native Dates without satisfying a tight Date interface — the pure
 * helpers don't read them. Storage adapters narrow the type at I/O boundaries.
 */
export interface ConversationState {
    id: string;        // phone number
    phone: string;
    state: 'idle' | 'collecting_info' | 'confirming' | 'awaiting_response';
    collectedData: Partial<{
        name: string;
        appointmentDate: string;
        slotTime: string;
        groupSize: number;
        weddingDate: string;
        phone: string;
    }>;
    missingFields: string[];
    lastMessageAt: unknown;
    expiresAt: unknown;
}

export interface SmsLog {
    id: string;
    direction: 'inbound' | 'outbound';
    phone: string;
    message: string;
    twilioSid?: string;
    parsedIntent?: string;
    createdAt: Date;
}
