import Anthropic from '@anthropic-ai/sdk';
import { config } from './config.js';

let client: Anthropic | null = null;

function getClient(): Anthropic {
  if (!client) {
    if (!config.anthropic.apiKey) {
      throw new Error('ANTHROPIC_API_KEY not set');
    }
    client = new Anthropic({ apiKey: config.anthropic.apiKey });
  }
  return client;
}

export interface LLMResponse {
  text: string;
  inputTokens: number;
  outputTokens: number;
  model: string;
}

export async function plannerCall(
  systemPrompt: string,
  userMessage: string,
  maxTokens = 2048,
): Promise<LLMResponse> {
  const anthropic = getClient();
  const model = config.anthropic.plannerModel;

  const response = await anthropic.messages.create({
    model,
    max_tokens: maxTokens,
    system: systemPrompt,
    messages: [{ role: 'user', content: userMessage }],
  });

  const text = response.content
    .filter((b): b is Anthropic.TextBlock => b.type === 'text')
    .map((b) => b.text)
    .join('');

  return {
    text,
    inputTokens: response.usage.input_tokens,
    outputTokens: response.usage.output_tokens,
    model,
  };
}

export async function executorCall(
  systemPrompt: string,
  userMessage: string,
  maxTokens = 2048,
): Promise<LLMResponse> {
  const anthropic = getClient();
  const model = config.anthropic.executorModel;

  const response = await anthropic.messages.create({
    model,
    max_tokens: maxTokens,
    system: systemPrompt,
    messages: [{ role: 'user', content: userMessage }],
  });

  const text = response.content
    .filter((b): b is Anthropic.TextBlock => b.type === 'text')
    .map((b) => b.text)
    .join('');

  return {
    text,
    inputTokens: response.usage.input_tokens,
    outputTokens: response.usage.output_tokens,
    model,
  };
}

// Claude Sonnet pricing: $3/M input, $15/M output
export function estimateCost(inputTokens: number, outputTokens: number): number {
  return (inputTokens * 3 + outputTokens * 15) / 1_000_000;
}
