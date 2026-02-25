import { GoogleGenerativeAI } from '@google/generative-ai';
import { config } from './config.js';

let genAI: GoogleGenerativeAI | null = null;

function getClient(): GoogleGenerativeAI {
  if (!genAI) {
    if (!config.gemini.apiKey) {
      throw new Error('GOOGLE_API_KEY not set');
    }
    genAI = new GoogleGenerativeAI(config.gemini.apiKey);
  }
  return genAI;
}

export interface GeminiResponse {
  text: string;
  inputTokens: number;
  outputTokens: number;
  model: string;
}

export async function criticCall(
  systemPrompt: string,
  userMessage: string,
  maxTokens = 2048,
): Promise<GeminiResponse> {
  const ai = getClient();
  const model = config.gemini.criticModel;

  const generativeModel = ai.getGenerativeModel({
    model,
    systemInstruction: systemPrompt,
    generationConfig: {
      maxOutputTokens: maxTokens,
      responseMimeType: 'application/json',
    },
  });

  const result = await generativeModel.generateContent(userMessage);
  const response = result.response;

  // Extract text — handle truncated or blocked responses
  let text = '';
  const candidate = response.candidates?.[0];
  const parts = candidate?.content?.parts;
  if (parts) {
    text = parts.map((p: any) => p.text || '').join('');
  }
  if (!text) {
    try { text = response.text(); } catch { /* already tried parts */ }
  }

  const finishReason = candidate?.finishReason || 'unknown';
  if (!text) {
    throw new Error(`Gemini returned empty response (finishReason: ${finishReason})`);
  }

  if (finishReason === 'MAX_TOKENS') {
    console.warn('[Gemini] Response was truncated (MAX_TOKENS). Attempting to use partial response.');
  }

  const usage = response.usageMetadata;

  return {
    text,
    inputTokens: usage?.promptTokenCount ?? 0,
    outputTokens: usage?.candidatesTokenCount ?? 0,
    model,
  };
}

// Gemini 2.5 Pro pricing: $1.25/M input, $10/M output (up to 200k)
export function estimateCost(inputTokens: number, outputTokens: number): number {
  return (inputTokens * 1.25 + outputTokens * 10) / 1_000_000;
}
