import { plannerCall, estimateCost } from '../lib/anthropic-client.js';
import { logAudit } from '../lib/audit.js';
import { createAgentRun, updateRunStatus } from './agent-store.js';
import type { PlannerOutput, AgentContext, RiskClass } from './types.js';

const PLANNER_SYSTEM_PROMPT = `You are the PLANNER agent for duSraBheja, a solo AI command center. Your job is to create actionable plans for the user's task.

For each task, generate 2-3 plan options with different approaches. For each option, specify:
1. A short label (e.g., "Quick Analysis", "Deep Dive", "Conservative")
2. Concrete steps (3-6 steps each)
3. Risk class: R0 (read-only), R1 (local write), R2 (external write), R3 (destructive)
4. Estimated cost in USD (based on API calls needed)
5. Brief rationale

Also provide:
- Your recommended option index (0-based)
- Your reasoning for the recommendation

IMPORTANT: Be practical. This is a solo developer's personal AI assistant, not an enterprise system.

Respond in valid JSON only:
{
  "options": [
    {
      "label": "string",
      "steps": ["step 1", "step 2"],
      "riskClass": "R0|R1|R2|R3",
      "estimatedCostUsd": 0.05,
      "rationale": "string"
    }
  ],
  "recommendedIndex": 0,
  "reasoning": "string"
}`;

export async function generatePlan(
  taskDescription: string,
  context: AgentContext,
  brainContext?: string,
): Promise<{ plan: PlannerOutput; runId: string; tokens: number; cost: number }> {
  const start = Date.now();

  const runId = await createAgentRun(
    'planner',
    taskDescription,
    'plan',
    'R0',
    context.triggeredBy,
    { taskDescription, brainContext: brainContext?.substring(0, 500) },
  );
  await updateRunStatus(runId, 'running');

  try {
    let userMessage = `Task: ${taskDescription}`;
    if (brainContext) {
      userMessage += `\n\nRelevant brain context:\n${brainContext}`;
    }

    const response = await plannerCall(PLANNER_SYSTEM_PROMPT, userMessage);

    // Strip markdown code fences if present
    let cleanText = response.text.trim();
    cleanText = cleanText.replace(/^```(?:json)?\s*\n?/i, '').replace(/\n?```\s*$/i, '');

    const jsonMatch = cleanText.match(/\{[\s\S]*\}/);
    if (!jsonMatch) {
      console.error('[Planner] Raw response (no JSON found):', response.text.substring(0, 500));
      throw new Error('Planner returned no valid JSON');
    }

    let parsed: PlannerOutput;
    try {
      parsed = JSON.parse(jsonMatch[0]) as PlannerOutput;
    } catch (parseErr) {
      console.error('[Planner] JSON parse failed:', (parseErr as Error).message);
      throw new Error('Planner returned invalid JSON');
    }

    // Validate
    if (!parsed.options || parsed.options.length === 0) {
      throw new Error('Planner returned no plan options');
    }

    const tokens = response.inputTokens + response.outputTokens;
    const cost = estimateCost(response.inputTokens, response.outputTokens);
    const durationMs = Date.now() - start;

    await updateRunStatus(runId, 'completed', parsed, null, response.model, tokens, cost, durationMs);

    await logAudit(
      {
        agentName: 'planner',
        actionType: 'generate_plan',
        riskClass: 'R0',
        modelUsed: response.model,
        tokensUsed: tokens,
        costUsd: cost,
        durationMs,
        inputSummary: taskDescription.substring(0, 100),
        outputSummary: `${parsed.options.length} options, recommended: ${parsed.options[parsed.recommendedIndex]?.label}`,
        decision: 'auto_approved',
      },
      context.traceId,
    );

    return { plan: parsed, runId, tokens, cost };
  } catch (err) {
    const durationMs = Date.now() - start;
    await updateRunStatus(runId, 'failed', null, (err as Error).message, null, null, null, durationMs);

    await logAudit(
      {
        agentName: 'planner',
        actionType: 'generate_plan',
        riskClass: 'R0',
        error: (err as Error).message,
        durationMs,
        inputSummary: taskDescription.substring(0, 100),
      },
      context.traceId,
    );

    throw err;
  }
}
