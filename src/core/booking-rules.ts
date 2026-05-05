// Pure booking-rules — no I/O, no storage. Adapters call these to validate
// incoming requests before hitting the BookingStore.
//
// Slots are gemach-specific defaults but exposed as overridable so other
// businesses (cold-calling, SaaS receptionist) can pass their own.

import type { Booking } from './types';
import { isValidAppointmentTime } from './date-utils';

export const APPOINTMENT_SLOTS = {
    wednesday: ['11:30 AM', '11:45 AM', '12:00 PM', '12:15 PM'],
    motzeiShabbos: ['7:30 PM', '7:45 PM', '8:00 PM', '8:15 PM', '8:30 PM', '8:45 PM', '9:00 PM', '9:15 PM'],
} as const;

/**
 * Whether a Date falls on a known appointment day-of-week (Wed=3, Sat=6 for gemach).
 * Override `validDays` for businesses with different schedules.
 */
export function isAppointmentDay(date: Date, validDays: number[] = [3, 6]): boolean {
    return validDays.includes(date.getDay());
}

/**
 * Group-size validation. Groups of 5–6 must take the last slot of the evening
 * (which has 30-minute slot duration). Groups of 7+ are rejected.
 *
 * Returns null on success, or an error message on failure.
 */
export function validateGroupSizeForSlot(
    groupSize: number,
    slotTime: string,
    appointmentDate: Date,
    slots: typeof APPOINTMENT_SLOTS = APPOINTMENT_SLOTS,
): string | null {
    if (groupSize <= 4) return null;
    if (groupSize > 6) return 'Maximum group size is 6';

    const dow = appointmentDate.getDay();
    const slotList = dow === 3 ? slots.wednesday : slots.motzeiShabbos;
    const lastSlot = slotList[slotList.length - 1];

    if (slotTime !== lastSlot) {
        return 'Groups of 5-6 need the last slot of the evening';
    }
    return null;
}

/**
 * Date-range validation: appointment must be in [today, today + 1 year].
 * Wedding date must be after appointment date and not in the past.
 */
export function validateBookingDates(
    appointmentDate: Date,
    weddingDate: Date,
    now: Date = new Date(),
): string | null {
    const yesterday = new Date(now);
    yesterday.setDate(yesterday.getDate() - 1);
    const oneYearForward = new Date(now);
    oneYearForward.setFullYear(now.getFullYear() + 1);

    if (appointmentDate < yesterday) return 'Appointment date cannot be in the past';
    if (appointmentDate > oneYearForward) return 'Appointment date must be within the next year';
    if (weddingDate < yesterday) return 'Wedding date cannot be in the past';
    if (weddingDate < appointmentDate) return 'Wedding date must be after appointment date';
    return null;
}

/**
 * Slot-time validation against the gemach's hard-coded schedule.
 * Re-exports the gemach-specific helper from date-utils for convenience.
 */
export { isValidAppointmentTime };

/**
 * Compute slot duration based on group size (gemach business rule).
 */
export function slotDurationForGroup(groupSize: number): number {
    return groupSize > 4 ? 30 : 15;
}

/**
 * Booleans + flags an adapter should set on a freshly-created booking.
 * Defaults defined here so every storage backend agrees on initial state.
 */
export function freshBookingDefaults(): Pick<
    Booking,
    'confirmationSent' | 'dayBeforeReminderSent' | 'sameDayReminderSent' | 'returnReminderSent'
        | 'gownSelected' | 'gownPickedUp' | 'gownReturned' | 'donationPaid'
> {
    return {
        confirmationSent: true,
        dayBeforeReminderSent: false,
        sameDayReminderSent: false,
        returnReminderSent: false,
        gownSelected: false,
        gownPickedUp: false,
        gownReturned: false,
        donationPaid: false,
    };
}
