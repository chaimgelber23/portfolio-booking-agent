// Date utilities for appointment scheduling
// All dates are handled in Eastern Time (America/New_York) for Brooklyn

/**
 * Get current date/time in Eastern Time
 */
export function toEasternDate(date: Date = new Date()): Date {
    // Convert to Eastern Time string, then parse back
    const eastern = new Date(date.toLocaleString('en-US', { timeZone: 'America/New_York' }));
    return eastern;
}

/**
 * Returns America/New_York offset from UTC in minutes for the given instant.
 * EDT = -240, EST = -300. Uses Intl so DST transitions are exact.
 */
export function getETOffsetMinutes(date: Date = new Date()): number {
    const parts = new Intl.DateTimeFormat('en-US', {
        timeZone: 'America/New_York',
        timeZoneName: 'longOffset',
    }).formatToParts(date);
    const offsetStr = parts.find((p) => p.type === 'timeZoneName')?.value;
    const m = offsetStr?.match(/GMT([+-])(\d{2}):(\d{2})/);
    if (!m) return -300; // fallback to EST
    const sign = m[1] === '+' ? 1 : -1;
    return sign * (parseInt(m[2]) * 60 + parseInt(m[3]));
}

/**
 * Build a Date whose wall-clock time in America/New_York equals (year, month1to12, day, hour, minute).
 * Handles DST automatically — e.g. makeETDate(2026, 5, 6, 8, 30) returns the UTC instant
 * corresponding to 2026-05-06 08:30 EDT.
 */
export function makeETDate(
    year: number,
    month1to12: number,
    day: number,
    hour: number,
    minute: number,
): Date {
    // Treat inputs as if they were UTC, then shift by the ET offset for that approximate moment.
    const guessUtc = Date.UTC(year, month1to12 - 1, day, hour, minute);
    const offsetMin = getETOffsetMinutes(new Date(guessUtc));
    return new Date(guessUtc - offsetMin * 60_000);
}

/**
 * Extract (year, month1to12, day) of the given instant as observed in America/New_York.
 */
export function easternYMD(date: Date): { year: number; month: number; day: number } {
    const fmt = new Intl.DateTimeFormat('en-CA', {
        timeZone: 'America/New_York',
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
    });
    const parts = fmt.formatToParts(date);
    return {
        year: parseInt(parts.find((p) => p.type === 'year')!.value),
        month: parseInt(parts.find((p) => p.type === 'month')!.value),
        day: parseInt(parts.find((p) => p.type === 'day')!.value),
    };
}

/**
 * Return the same calendar date in ET, shifted by `days` (positive or negative).
 */
export function shiftEasternDays(date: Date, days: number): { year: number; month: number; day: number } {
    const ymd = easternYMD(date);
    // Use makeETDate at noon to get a stable instant inside the target day, then re-extract.
    const stable = makeETDate(ymd.year, ymd.month, ymd.day + days, 12, 0);
    return easternYMD(stable);
}

/**
 * Normalize common day-name variants from voice transcription.
 * Voice STT mishears "motzei" in many ways; callers also say "Saturday night".
 * Collapse all of these to canonical "motzei shabbos" / "shabbos" before regex.
 */
function normalizeDayPhrase(s: string): string {
    let out = s;
    // Order matters: catch multi-word phrases first
    out = out.replace(/\b(sat|saturday)\s*(night|nite|evening|eve)\b/gi, 'motzei shabbos');
    out = out.replace(/\b(motza?ei|motzaei|motzai|motzay|motzee|moetzei|moatzei|moetzai|mosaei|motzi|mo[ts]ay|moat[- ]?say)\b\s*(shabbos|shabbas|shabbat|shabbis|shabis)\b/gi, 'motzei shabbos');
    // Lone "motzei" / "motzai" (no shabbos word) — caller almost always means motzei shabbos
    out = out.replace(/\b(motza?ei|motzaei|motzai|motzay|motzee|moetzei|moatzei)\b(?!\s*shabbos)/gi, 'motzei shabbos');
    // Standalone shabbos variants → shabbos (handled as Saturday)
    out = out.replace(/\b(shabbas|shabbat|shabbis|shabis)\b/gi, 'shabbos');
    return out;
}

/**
 * Parse natural language date to Date object
 */
export function parseDate(dateStr: string, referenceDate?: Date): Date | null {
    const lower = normalizeDayPhrase(dateStr.toLowerCase().trim());
    const today = new Date(referenceDate || toEasternDate());
    today.setHours(0, 0, 0, 0);

    // Handle relative dates
    if (lower === 'today') {
        const result = new Date(today);
        result.setHours(12, 0, 0, 0);
        return result;
    }

    if (lower === 'tomorrow') {
        const tomorrow = new Date(today);
        tomorrow.setDate(tomorrow.getDate() + 1);
        tomorrow.setHours(12, 0, 0, 0);
        return tomorrow;
    }

    // Try to parse as a standard date (only if it includes a year)
    if (/\d{4}/.test(dateStr) || /\d{1,2}\/\d{1,2}\/\d{2,4}/.test(dateStr)) {
        const parsed = new Date(dateStr);
        if (!isNaN(parsed.getTime())) {
            parsed.setHours(12, 0, 0, 0);
            return parsed;
        }
    }

    // Try specific month+day formats BEFORE relative day names
    const monthDayMatch = lower.match(/(\w+)\s+(\d{1,2})(?:st|nd|rd|th)?/);
    if (monthDayMatch) {
        const months = ['january', 'february', 'march', 'april', 'may', 'june',
            'july', 'august', 'september', 'october', 'november', 'december'];
        const monthIndex = months.findIndex(m => monthDayMatch[1].startsWith(m.slice(0, 3)));
        if (monthIndex !== -1) {
            const day = parseInt(monthDayMatch[2]);
            let result = new Date(today.getFullYear(), monthIndex, day, 12, 0, 0, 0);

            if (result < today) {
                result.setFullYear(result.getFullYear() + 1);
            }

            const monthsInFuture = (result.getTime() - today.getTime()) / (1000 * 60 * 60 * 24 * 30);
            if (monthsInFuture > 8) {
                result.setFullYear(result.getFullYear() - 1);
                if (result < today) {
                    result.setFullYear(result.getFullYear() + 1);
                }
            }

            return result;
        }
    }

    // Handle relative day names: "this wednesday", "next wednesday", "motzei shabbos", etc.
    const dayNames = ['sunday', 'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday'];
    const dayMatch = lower.match(/(this|next)?\s*(sunday|monday|tuesday|wednesday|thursday|friday|saturday|shabbos|motzei shabbos)/i);

    if (dayMatch) {
        let targetDay = dayNames.indexOf(dayMatch[2].toLowerCase());

        if (dayMatch[2].toLowerCase().includes('shabbos')) {
            targetDay = 6;
        }

        if (targetDay === -1) return null;

        const result = new Date(today);
        const currentDay = result.getDay();
        let daysToAdd = targetDay - currentDay;

        if (dayMatch[1]?.toLowerCase() === 'next' || daysToAdd <= 0) {
            daysToAdd += 7;
        }

        result.setDate(result.getDate() + daysToAdd);
        result.setHours(12, 0, 0, 0);
        return result;
    }

    return null;
}

/**
 * Parse time string to hours and minutes
 */
export function parseTime(timeStr: string): { hours: number; minutes: number } | null {
    const match = timeStr.match(/(\d{1,2}):?(\d{2})?\s*(am|pm)?/i);
    if (!match) return null;

    let hours = parseInt(match[1]);
    const minutes = match[2] ? parseInt(match[2]) : 0;
    const period = match[3]?.toLowerCase();

    if (period === 'pm' && hours !== 12) {
        hours += 12;
    } else if (period === 'am' && hours === 12) {
        hours = 0;
    }

    return { hours, minutes };
}

/**
 * Check if a date/time is a valid appointment time
 */
export function isValidAppointmentTime(date: Date, timeStr: string): boolean {
    const dayOfWeek = date.getDay();
    const time = parseTime(timeStr);

    if (!time) return false;

    // Wednesday: 11:30 AM – 12:30 PM
    if (dayOfWeek === 3) {
        const hour = time.hours;
        const minute = time.minutes;

        if (hour === 11 && (minute === 30 || minute === 45)) return true;
        if (hour === 12 && (minute === 0 || minute === 15)) return true;
        return false;
    }

    // Saturday (Motzei Shabbos): 7:30 PM – 9:30 PM
    if (dayOfWeek === 6) {
        const hour = time.hours;
        const minute = time.minutes;

        if (hour >= 19 && hour <= 21) {
            if (minute === 0 || minute === 15 || minute === 30 || minute === 45) {
                if (hour === 21 && minute > 15) return false;
                return true;
            }
        }
        return false;
    }

    return false;
}

/**
 * Get the next available appointment dates
 */
export function getNextAvailableDates(referenceDate: Date = new Date(), count: number = 4): Date[] {
    const dates: Date[] = [];
    const current = new Date(referenceDate);
    current.setHours(12, 0, 0, 0);

    while (dates.length < count) {
        current.setDate(current.getDate() + 1);
        const day = current.getDay();

        if (day === 3 || day === 6) {
            dates.push(new Date(current));
        }
    }

    return dates;
}

/**
 * Format date for display
 */
export function formatDate(date: Date): string {
    return date.toLocaleDateString('en-US', {
        weekday: 'long',
        month: 'long',
        day: 'numeric',
        timeZone: 'America/New_York',
    });
}

/**
 * Format date short for lists
 */
export function formatDateShort(date: Date): string {
    return date.toLocaleDateString('en-US', {
        weekday: 'short',
        month: 'short',
        day: 'numeric',
        timeZone: 'America/New_York',
    });
}

/**
 * Get week start (Sunday) and end (Saturday) dates
 */
export function getWeekRange(referenceDate: Date = new Date()): { start: Date; end: Date } {
    const start = new Date(referenceDate);
    start.setHours(0, 0, 0, 0);
    start.setDate(start.getDate() - start.getDay());

    const end = new Date(start);
    end.setDate(end.getDate() + 6);
    end.setHours(23, 59, 59, 999);

    return { start, end };
}

/**
 * Get next week's range
 */
export function getNextWeekRange(referenceDate: Date = new Date()): { start: Date; end: Date } {
    const thisWeek = getWeekRange(referenceDate);
    const start = new Date(thisWeek.start);
    start.setDate(start.getDate() + 7);

    const end = new Date(start);
    end.setDate(end.getDate() + 6);
    end.setHours(23, 59, 59, 999);

    return { start, end };
}
