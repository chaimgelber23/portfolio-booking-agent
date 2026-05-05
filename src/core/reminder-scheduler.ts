// Reminder due-window scheduler.
//
// Purpose: cron-fire-time and appointment-time decoupling. Cron runs every N
// minutes; per booking, we compute a target send-time and only fire if `now`
// is inside [target − preMs, target + postMs]. This eliminates whole classes
// of bugs (UTC-vs-local, DST shifts, "cron fired after appointment").
//
// Send-time policy is encoded here. Per-business overrides are accepted via
// the optional second argument to `sameDayTargetTime`.

import { easternYMD, makeETDate, shiftEasternDays } from './date-utils';

export interface SendTimePolicy {
    /** Wall-clock hour in ET when same-day reminders should fire for Wednesday-style appointments. */
    wednesdayHourET: number;
    /** Wall-clock minute in ET. */
    wednesdayMinuteET: number;
    /** Wall-clock hour in ET on the FRIDAY before, for Saturday-night (Motzei Shabbos) appointments. */
    motzeiShabbosFridayHourET: number;
    motzeiShabbosFridayMinuteET: number;
}

export const DEFAULT_GEMACH_POLICY: SendTimePolicy = {
    wednesdayHourET: 8,        // 8:30 AM ET → 3h before earliest 11:30 AM Wed slot
    wednesdayMinuteET: 30,
    motzeiShabbosFridayHourET: 15,  // 3:00 PM ET on Friday — well before Shabbos starts
    motzeiShabbosFridayMinuteET: 0,
};

/**
 * Compute the target send-time for a same-day reminder.
 * Wednesday → wednesdayHourET on appointment day.
 * Saturday (Motzei Shabbos) → motzeiShabbosFridayHourET on the Friday before.
 *
 * Returns null if the appointment is on a day that isn't recognized.
 */
export function sameDayTargetTime(
    appointment: Date,
    policy: SendTimePolicy = DEFAULT_GEMACH_POLICY,
): Date | null {
    const apptYMD = easternYMD(appointment);
    const noonOfAppt = makeETDate(apptYMD.year, apptYMD.month, apptYMD.day, 12, 0);
    const dayOfWeek = new Intl.DateTimeFormat('en-US', {
        timeZone: 'America/New_York',
        weekday: 'short',
    }).format(noonOfAppt);

    if (dayOfWeek === 'Wed') {
        return makeETDate(
            apptYMD.year, apptYMD.month, apptYMD.day,
            policy.wednesdayHourET, policy.wednesdayMinuteET,
        );
    }
    if (dayOfWeek === 'Sat') {
        const fri = shiftEasternDays(appointment, -1);
        return makeETDate(
            fri.year, fri.month, fri.day,
            policy.motzeiShabbosFridayHourET, policy.motzeiShabbosFridayMinuteET,
        );
    }
    return null;
}

/**
 * Compute the target send-time for a day-before reminder.
 * Default policy: 10:00 AM ET on the previous calendar day in ET.
 * Both Wed appointments and Motzei Shabbos appointments get this on a normal weekday morning.
 */
export function dayBeforeTargetTime(appointment: Date, hourET = 10, minuteET = 0): Date {
    const prev = shiftEasternDays(appointment, -1);
    return makeETDate(prev.year, prev.month, prev.day, hourET, minuteET);
}

export type FireWindowResult =
    | { fire: true }
    | { fire: false; reason: 'before-window' | 'after-window' | 'no-target' };

/**
 * Is `now` inside the [target − preMs, target + postMs] fire-window?
 * Used by the cron route to decide whether to send for this booking.
 */
export function isInFireWindow(
    target: Date | null,
    now: Date,
    preMs: number,
    postMs: number,
): FireWindowResult {
    if (!target) return { fire: false, reason: 'no-target' };
    const drift = now.getTime() - target.getTime();
    if (drift < -preMs) return { fire: false, reason: 'before-window' };
    if (drift > postMs) return { fire: false, reason: 'after-window' };
    return { fire: true };
}
