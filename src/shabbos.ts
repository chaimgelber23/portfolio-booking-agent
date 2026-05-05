// Hebrew Calendar integration for Jewish holiday awareness
// Uses @hebcal/core to determine which dates should block appointments

import { HebrewCalendar, HDate, flags } from '@hebcal/core';

/**
 * Holiday blocking categories for an Orthodox gemach
 */
export interface HolidayBlockInfo {
    blocked: boolean;
    reason?: string;
    category?: 'yomTov' | 'cholHamoed' | 'erev' | 'fast' | 'threeWeeks' | 'shabbos';
}

/**
 * Check if a date falls on a Jewish holiday that should block appointments.
 *
 * Full blocks:
 * - Yom Tov (Rosh Hashanah, Yom Kippur, Sukkos 1-2, Shmini Atzeres/Simchas Torah,
 *   Pesach 1-2 & 7-8, Shavuos 1-2)
 * - Chol HaMoed (Sukkos, Pesach)
 * - Erev Yom Tov (afternoon - blocks all appointment times)
 * - Tisha B'Av
 * - Erev Shabbos (Friday) is already not an appointment day
 * - Shabbos is already Saturday appointment = Motzei Shabbos (after Shabbos ends)
 *
 * Note: Saturday (Motzei Shabbos) appointments are AFTER Shabbos ends,
 * so regular Shabbos does NOT block Saturday appointments.
 * However, if Saturday is also Yom Tov, it IS blocked.
 */
export function isJewishHoliday(date: Date): HolidayBlockInfo {
    const hd = new HDate(date);
    const dayOfWeek = date.getDay();

    // Get holidays for this date (diaspora = false for il, true for diaspora)
    const events = HebrewCalendar.getHolidaysOnDate(hd, false) || [];

    for (const ev of events) {
        const mask = ev.getFlags();
        const desc = ev.getDesc();

        // Yom Tov - full day block
        if (mask & flags.CHAG) {
            // Special case: Saturday appointments are Motzei Shabbos (after Shabbos/Yom Tov)
            // If it's a regular Shabbos that's also the last day of Yom Tov,
            // Motzei Shabbos appointments might still be blocked if Yom Tov ends late.
            // For safety, block all Yom Tov days including Saturday.
            return {
                blocked: true,
                reason: desc,
                category: 'yomTov',
            };
        }

        // Chol HaMoed - gemach closed in frum communities
        if (mask & flags.CHOL_HAMOED) {
            return {
                blocked: true,
                reason: `Chol HaMoed - ${desc}`,
                category: 'cholHamoed',
            };
        }

        // Major fast days (Tisha B'Av, Yom Kippur)
        // Yom Kippur is already caught by CHAG, but Tisha B'Av is MAJOR_FAST
        if (mask & flags.MAJOR_FAST) {
            return {
                blocked: true,
                reason: desc,
                category: 'fast',
            };
        }

        // Erev Yom Tov - block appointments (people are preparing)
        // This catches Erev Rosh Hashanah, Erev Yom Kippur, Erev Sukkos,
        // Erev Pesach, Erev Shavuos
        if (desc.startsWith('Erev') && (
            desc.includes('Rosh Hashana') ||
            desc.includes('Yom Kippur') ||
            desc.includes('Sukkot') ||
            desc.includes('Pesach') ||
            desc.includes('Shavuot')
        )) {
            return {
                blocked: true,
                reason: desc,
                category: 'erev',
            };
        }
    }

    // Check for the Three Weeks (17 Tammuz through 9 Av)
    // No weddings during this period = minimal demand
    const threeWeeksBlock = isThreeWeeks(hd);
    if (threeWeeksBlock) {
        return {
            blocked: true,
            reason: threeWeeksBlock,
            category: 'threeWeeks',
        };
    }

    return { blocked: false };
}

/**
 * Check if a Hebrew date falls during the Three Weeks (17 Tammuz - 9 Av)
 * This is a period when weddings don't take place, so gown fittings
 * are not needed. The gemach can stay open for returns.
 */
function isThreeWeeks(hd: HDate): string | null {
    const month = hd.getMonth(); // Tammuz = 4 (in regular year), Av = 5
    const day = hd.getDate();

    // In @hebcal/core, months are 1-indexed:
    // Nisan=1, Iyar=2, Sivan=3, Tammuz=4, Av=5, Elul=6
    // Tishrei=7, Cheshvan=8, Kislev=9, Teves=10, Shvat=11, Adar=12 (Adar II=13 in leap year)

    // 17 Tammuz through 9 Av
    const TAMMUZ = 4;
    const AV = 5;

    if (month === TAMMUZ && day >= 17) {
        return 'Three Weeks (17 Tammuz - 9 Av)';
    }

    if (month === AV && day <= 9) {
        return 'Three Weeks (17 Tammuz - 9 Av)';
    }

    return null;
}

/**
 * Get all blocked dates for a given Gregorian date range.
 * Useful for calendar display - shows which dates have Jewish holidays.
 */
export function getBlockedDatesInRange(
    startDate: Date,
    endDate: Date
): { date: Date; reason: string; category: string }[] {
    const blocked: { date: Date; reason: string; category: string }[] = [];
    const current = new Date(startDate);
    current.setHours(12, 0, 0, 0);

    while (current <= endDate) {
        const result = isJewishHoliday(current);
        if (result.blocked) {
            blocked.push({
                date: new Date(current),
                reason: result.reason || 'Jewish Holiday',
                category: result.category || 'yomTov',
            });
        }
        current.setDate(current.getDate() + 1);
    }

    return blocked;
}

/**
 * Get upcoming Jewish holidays that would affect appointments.
 * Returns the next N holidays within a time range.
 */
export function getUpcomingHolidays(
    fromDate: Date = new Date(),
    months: number = 3
): { date: Date; name: string; category: string }[] {
    const endDate = new Date(fromDate);
    endDate.setMonth(endDate.getMonth() + months);

    const events = HebrewCalendar.calendar({
        start: fromDate,
        end: endDate,
        il: false, // Diaspora
    });

    const holidays: { date: Date; name: string; category: string }[] = [];

    for (const ev of events) {
        const mask = ev.getFlags();
        const date = ev.date.greg();

        if (mask & flags.CHAG) {
            holidays.push({ date, name: ev.getDesc(), category: 'yomTov' });
        } else if (mask & flags.CHOL_HAMOED) {
            holidays.push({ date, name: ev.getDesc(), category: 'cholHamoed' });
        } else if (mask & flags.MAJOR_FAST) {
            holidays.push({ date, name: ev.getDesc(), category: 'fast' });
        }
    }

    return holidays;
}
