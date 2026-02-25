import { Ollama } from 'ollama';
import { config } from './config.js';

const ollama = new Ollama({ host: config.ollama.host });

export interface ClassificationResult {
  category: string;       // idea, task, note, question, link, voice
  confidence: number;     // 0.0 to 1.0
  priority: string;       // low, medium, high, urgent
  nextAction: string;     // suggested next action
  summary: string;        // one-line summary
  suggestedProject: string | null;  // matched project name or null
}

const CLASSIFY_PROMPT = `You are a personal knowledge assistant. Classify the following message into exactly one category and extract metadata.

Categories:
- idea: A new concept, thought, or creative insight
- task: Something that needs to be done, an action item
- note: General information, observation, or reference
- question: Something the user wants to investigate or answer
- link: A URL or reference to external content

Respond in valid JSON only, no other text:
{
  "category": "<one of: idea, task, note, question, link>",
  "confidence": <0.0 to 1.0>,
  "priority": "<one of: low, medium, high, urgent>",
  "nextAction": "<suggested next step in under 15 words>",
  "summary": "<one-line summary of the message in under 20 words>"
}

Message: `;

export async function classify(text: string): Promise<ClassificationResult> {
  const response = await ollama.generate({
    model: config.ollama.classifyModel,
    prompt: CLASSIFY_PROMPT + text,
    stream: false,
    options: {
      temperature: 0.1,
      num_predict: 256,
    },
  });

  const raw = response.response.trim();
  // Extract JSON from the response (handle markdown code blocks)
  const jsonMatch = raw.match(/\{[\s\S]*\}/);
  if (!jsonMatch) {
    throw new Error(`Failed to parse classification response: ${raw.substring(0, 200)}`);
  }

  const parsed = JSON.parse(jsonMatch[0]);

  return {
    category: parsed.category || 'note',
    confidence: Math.max(0, Math.min(1, parseFloat(parsed.confidence) || 0.5)),
    priority: parsed.priority || 'medium',
    nextAction: parsed.nextAction || '',
    summary: parsed.summary || text.substring(0, 80),
    suggestedProject: null,
  };
}

export async function classifyWithProjects(
  text: string,
  projectNames: string[],
): Promise<ClassificationResult> {
  const projectList = projectNames.length > 0
    ? `\n\nActive projects: ${projectNames.join(', ')}\nIf the message relates to one of these projects, set "suggestedProject" to that project name. Otherwise set it to null.`
    : '';

  const prompt = `You are a personal knowledge assistant. Classify the following message into exactly one category and extract metadata.

Categories:
- idea: A new concept, thought, or creative insight
- task: Something that needs to be done, an action item
- note: General information, observation, or reference
- question: Something the user wants to investigate or answer
- link: A URL or reference to external content${projectList}

Respond in valid JSON only, no other text:
{
  "category": "<one of: idea, task, note, question, link>",
  "confidence": <0.0 to 1.0>,
  "priority": "<one of: low, medium, high, urgent>",
  "nextAction": "<suggested next step in under 15 words>",
  "summary": "<one-line summary of the message in under 20 words>",
  "suggestedProject": "<project name or null>"
}

Message: ${text}`;

  const response = await ollama.generate({
    model: config.ollama.classifyModel,
    prompt,
    stream: false,
    options: {
      temperature: 0.1,
      num_predict: 300,
    },
  });

  const raw = response.response.trim();
  const jsonMatch = raw.match(/\{[\s\S]*\}/);
  if (!jsonMatch) {
    throw new Error(`Failed to parse classification response: ${raw.substring(0, 200)}`);
  }

  const parsed = JSON.parse(jsonMatch[0]);

  // Validate suggestedProject against actual project names (case-insensitive)
  let suggestedProject: string | null = null;
  if (parsed.suggestedProject && parsed.suggestedProject !== 'null') {
    const match = projectNames.find(
      (p) => p.toLowerCase() === String(parsed.suggestedProject).toLowerCase(),
    );
    suggestedProject = match || null;
  }

  return {
    category: parsed.category || 'note',
    confidence: Math.max(0, Math.min(1, parseFloat(parsed.confidence) || 0.5)),
    priority: parsed.priority || 'medium',
    nextAction: parsed.nextAction || '',
    summary: parsed.summary || text.substring(0, 80),
    suggestedProject,
  };
}

export async function generateEmbedding(text: string): Promise<number[]> {
  const response = await ollama.embed({
    model: config.ollama.embedModel,
    input: text,
  });
  return response.embeddings[0];
}

export async function summarize(text: string): Promise<string> {
  const response = await ollama.generate({
    model: config.ollama.summaryModel,
    prompt: `Summarize the following items into a concise daily briefing for a solo developer/founder. Be direct, use bullet points. Focus on what needs attention today.\n\n${text}`,
    stream: false,
    options: {
      temperature: 0.3,
      num_predict: 512,
    },
  });
  return response.response.trim();
}
