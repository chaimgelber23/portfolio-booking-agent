// AI-powered SMS message parser using OpenAI

import OpenAI from 'openai';
import type { ConversationState } from './types';

const openai = new OpenAI({
    apiKey: process.env.OPENAI_API_KEY,
});

// Required fields for a complete booking
const REQUIRED_FIELDS = ['name', 'appointmentDate', 'groupSize', 'weddingDate', 'phone'] as const;

export interface ParsedMessage {
    intent: 'booking' | 'question' | 'confirmation' | 'cancellation' | 'reschedule' | 'greeting' | 'unknown';
    extractedData: Partial<{
        name: string;
        appointmentDate: string;
        slotTime: string;
        groupSize: number;
        weddingDate: string;
        phone: string;
    }>;
    question?: string;
    confidence: number;
    rawResponse?: string;
}

/**
 * Parse incoming SMS message using AI
 */
export async function parseMessage(
    message: string,
    existingState?: ConversationState | null
): Promise<ParsedMessage> {
    const existingData = existingState?.collectedData || {};

    const systemPrompt = `You are an assistant for Gelber Gown Gemach, a wedding gown lending service in Brooklyn.

Your job is to parse incoming SMS messages and extract booking information.

APPOINTMENT HOURS (important - only these times are valid):
- Wednesday: 11:30 AM – 12:30 PM
- Motzei Shabbos (Saturday night): 7:30 PM – 9:30 PM

REQUIRED BOOKING INFO:
1. Name
2. Appointment date and time (must be Wed or Motzei Shabbos)
3. Number of people in group (max 4, or 6 for 30-min slot)
4. Wedding date
5. Phone number

EXISTING DATA already collected:
${JSON.stringify(existingData, null, 2)}

Respond in JSON format:
{
  "intent": "booking" | "question" | "confirmation" | "cancellation" | "reschedule" | "greeting" | "unknown",
  "extractedData": {
    "name": "if found",
    "appointmentDate": "natural language date like 'this Wednesday' or 'January 25'",
    "slotTime": "if specified, like '11:30 AM' or '7:30 PM'",
    "groupSize": number,
    "weddingDate": "natural language date",
    "phone": "if found"
  },
  "question": "if intent is question, what are they asking about",
  "confidence": 0.0-1.0
}

RULES:
- Extract ONLY information explicitly stated in the message
- Do not make assumptions or fill in missing data
- If they're asking about hours, location, sizes, etc., intent is "question"
- If they say "yes", "confirm", "sounds good", intent is "confirmation"
- If they say "cancel", intent is "cancellation"
- If they say "reschedule", "move", "change my appointment", "different date", intent is "reschedule"
- If just "hi", "hello", intent is "greeting"`;

    try {
        const response = await openai.chat.completions.create({
            model: 'gpt-4o-mini',
            messages: [
                { role: 'system', content: systemPrompt },
                { role: 'user', content: message },
            ],
            response_format: { type: 'json_object' },
            temperature: 0.2,
        });

        const content = response.choices[0]?.message?.content;
        if (!content) {
            throw new Error('No response from AI');
        }

        const parsed = JSON.parse(content) as ParsedMessage;
        parsed.rawResponse = content;

        return parsed;
    } catch (error) {
        console.error('Failed to parse message:', error);
        return fallbackParse(message);
    }
}

/**
 * Fallback parser using simple keyword matching
 */
function fallbackParse(message: string): ParsedMessage {
    const lower = message.toLowerCase();

    if (lower.includes('cancel')) {
        return { intent: 'cancellation', extractedData: {}, confidence: 0.6 };
    }

    if (lower.includes('reschedule') || lower.includes('move my') || lower.includes('change my appointment') || lower.includes('different date')) {
        return { intent: 'reschedule', extractedData: {}, confidence: 0.6 };
    }

    if (['yes', 'confirm', 'sounds good', 'perfect', 'great'].some(w => lower.includes(w))) {
        return { intent: 'confirmation', extractedData: {}, confidence: 0.6 };
    }

    if (lower.includes('?') || ['where', 'when', 'how', 'what', 'can i'].some(w => lower.includes(w))) {
        return { intent: 'question', extractedData: {}, question: message, confidence: 0.5 };
    }

    if (['hi', 'hello', 'hey', 'shalom'].some(w => lower === w || lower.startsWith(w + ' '))) {
        return { intent: 'greeting', extractedData: {}, confidence: 0.7 };
    }

    const extractedData: ParsedMessage['extractedData'] = {};

    const phoneMatch = message.match(/\b(\d{3}[-.]?\d{3}[-.]?\d{4})\b/);
    if (phoneMatch) {
        extractedData.phone = phoneMatch[1];
    }

    const groupMatch = message.match(/(\d+)\s*(people|person|of us)/i);
    if (groupMatch) {
        extractedData.groupSize = parseInt(groupMatch[1]);
    }

    if (Object.keys(extractedData).length > 0) {
        return { intent: 'booking', extractedData, confidence: 0.4 };
    }

    return { intent: 'unknown', extractedData: {}, confidence: 0.2 };
}

/**
 * Check which required fields are still missing
 */
export function getMissingFields(data: Partial<ParsedMessage['extractedData']>): string[] {
    const missing: string[] = [];

    for (const field of REQUIRED_FIELDS) {
        if (!data[field]) {
            missing.push(field);
        }
    }

    return missing;
}

/**
 * Merge new data with existing conversation state
 */
export function mergeData(
    existing: Partial<ParsedMessage['extractedData']>,
    newData: Partial<ParsedMessage['extractedData']>
): Partial<ParsedMessage['extractedData']> {
    return {
        ...existing,
        ...Object.fromEntries(
            Object.entries(newData).filter(([_, v]) => v !== undefined && v !== null && v !== '')
        ),
    };
}
