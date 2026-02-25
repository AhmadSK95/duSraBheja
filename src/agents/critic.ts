import { criticCall, estimateCost } from '../lib/gemini-client.js';
import { logAudit } from '../lib/audit.js';
import { createAgentRun, updateRunStatus } from './agent-store.js';
import type { PlannerOutput, CriticOutput, AgentContext } from './types.js';

const CRITIC_SYSTEM_PROMPT = `You are the CRITIC agent for duSraBheja, a solo AI command center. You review plans created by the Planner agent (a DIFFERENT AI model).

Your job is ADVERSARIAL: actively look for flaws, risks, and issues. Be thorough but constructive.

Evaluate the plan on:
1. Feasibility — Can this actually be done with the available tools?
2. Risk — Are risk levels correctly assigned? Any hidden dangers?
3. Cost — Is the estimated cost reasonable?
4. Completeness — Are there missing steps or edge cases?
5. Safety — Could any step cause data loss, privacy issues, or unintended side effects?

Respond in valid JSON only:
{
  "approved": true|false,
  "score": 0-10,
  "issues": ["issue 1", "issue 2"],
  "suggestions": ["suggestion 1"],
  "reasoning": "Overall assessment"
}

Score guide: 0-3 reject, 4-6 needs work, 7-8 good, 9-10 excellent.
Approve if score >= 5 and no critical issues.`;

export async function reviewPlan(
  plan: PlannerOutput,
  taskDescription: string,
  context: AgentContext,
): Promise<{ review: CriticOutput; runId: string; tokens: number; cost: number }> {
  const start = Date.now();

  const runId = await createAgentRun(
    'critic',
    taskDescription,
    'review',
    'R0',
    context.triggeredBy,
    { taskDescription, optionCount: plan.options.length },
  );
  await updateRunStatus(runId, 'running');

  try {
    const planSummary = plan.options
      .map((opt, i) => {
        const marker = i === plan.recommendedIndex ? ' [RECOMMENDED]' : '';
        return `Option ${i + 1}: ${opt.label}${marker} (${opt.riskClass}, $${opt.estimatedCostUsd.toFixed(3)})\nSteps: ${opt.steps.join(' → ')}\nRationale: ${opt.rationale}`;
      })
      .join('\n\n');

    const userMessage = `Task: ${taskDescription}\n\nPlanner's recommendation: Option ${plan.recommendedIndex + 1}\nPlanner's reasoning: ${plan.reasoning}\n\n--- PLAN OPTIONS ---\n${planSummary}`;

    const response = await criticCall(CRITIC_SYSTEM_PROMPT, userMessage, 4096);

    // Strip markdown code fences if present
    let cleanText = response.text.trim();
    cleanText = cleanText.replace(/^```(?:json)?\s*\n?/i, '').replace(/\n?```\s*$/i, '');

    const jsonMatch = cleanText.match(/\{[\s\S]*\}/);
    if (!jsonMatch) {
      console.error('[Critic] Raw response (no JSON found):', response.text.substring(0, 500));
      throw new Error('Critic returned no valid JSON');
    }

    let parsed: CriticOutput;
    try {
      parsed = JSON.parse(jsonMatch[0]) as CriticOutput;
    } catch (parseErr) {
      console.error('[Critic] JSON parse failed:', (parseErr as Error).message, jsonMatch[0].substring(0, 300));
      throw new Error('Critic returned invalid JSON');
    }
    const tokens = response.inputTokens + response.outputTokens;
    const cost = estimateCost(response.inputTokens, response.outputTokens);
    const durationMs = Date.now() - start;

    await updateRunStatus(runId, 'completed', parsed, null, response.model, tokens, cost, durationMs);

    await logAudit(
      {
        agentName: 'critic',
        actionType: 'review_plan',
        riskClass: 'R0',
        modelUsed: response.model,
        tokensUsed: tokens,
        costUsd: cost,
        durationMs,
        inputSummary: taskDescription.substring(0, 100),
        outputSummary: `Score: ${parsed.score}/10, Approved: ${parsed.approved}, Issues: ${parsed.issues.length}`,
        decision: parsed.approved ? 'auto_approved' : 'denied',
      },
      context.traceId,
    );

    return { review: parsed, runId, tokens, cost };
  } catch (err) {
    const durationMs = Date.now() - start;
    await updateRunStatus(runId, 'failed', null, (err as Error).message, null, null, null, durationMs);

    await logAudit(
      {
        agentName: 'critic',
        actionType: 'review_plan',
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
