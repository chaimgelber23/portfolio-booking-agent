// Pure conversation-state helpers.
// I/O (Firestore reads/writes, expiration, etc.) is intentionally absent —
// concrete storage adapters in adapters/storage/ handle that, calling these
// pure functions for state-transition logic.

import type { ConversationState } from './types.js';
import { getMissingFields, mergeData, type ParsedMessage } from './message-parser.js';

export const CONVERSATION_TIMEOUT_MS = 60 * 60 * 1000; // 1 hour

/**
 * Build the next ConversationState given a parsed message + the existing state (if any).
 * Pure function — does not touch storage. Caller is responsible for persisting the result.
 */
export function nextConversationState(
    phone: string,
    parsed: ParsedMessage,
    existingState: ConversationState | null,
    now: Date = new Date(),
): ConversationState {
    const stateId = phone.replace(/\D/g, '');

    const collectedData = mergeData(
        existingState?.collectedData || {},
        parsed.extractedData,
    );

    if (!collectedData.phone) {
        collectedData.phone = phone;
    }

    const missingFields = getMissingFields(collectedData);

    return {
        id: stateId,
        phone,
        state: missingFields.length === 0 ? 'confirming' : 'collecting_info',
        collectedData,
        missingFields,
        lastMessageAt: now,
        expiresAt: new Date(now.getTime() + CONVERSATION_TIMEOUT_MS),
    };
}

/** Whether the collected data contains every required booking field. */
export function isBookingComplete(state: ConversationState): boolean {
    return state.missingFields.length === 0;
}

/** Whether a state has expired and should be ignored / deleted. */
export function isStateExpired(state: ConversationState, now: Date = new Date()): boolean {
    return state.expiresAt.getTime() < now.getTime();
}
