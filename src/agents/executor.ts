import { executorCall, estimateCost } from '../lib/anthropic-client.js';
import { logAudit } from '../lib/audit.js';
import { createAgentRun, updateRunStatus } from './agent-store.js';
import type { PlannerOutput, PlanOption, ExecutorOutput, AgentContext, RiskClass } from './types.js';

const EXECUTOR_SYSTEM_PROMPT = `You are the EXECUTOR agent for duSraBheja, a solo AI command center. You execute approved plans.

Your capabilities:
- Analyze code and provide insights
- Generate suggestions and recommendations
- Create summaries and documentation
- Propose GitHub issues and PR descriptions
- Search and synthesize knowledge from provided context

Your limitations (STRICT — do NOT attempt):
- NO filesystem writes or reads
- NO shell command execution
- NO git operations
- NO API calls to external services
- NO database modifications

For each step in the plan, describe what you did and any output/artifacts produced.

Respond in valid JSON only:
{
  "result": "Summary of what was accomplished",
  "actions": ["action 1 completed", "action 2 completed"],
  "artifacts": ["artifact description 1"]
}`;

export async function executePlan(
  plan: PlannerOutput,
  selectedOptionIndex: number,
  taskDescription: string,
  context: AgentContext,
): Promise<{ result: ExecutorOutput; runId: string; tokens: number; cost: number }> {
  const start = Date.now();
  const option: PlanOption = plan.options[selectedOptionIndex] || plan.options[0];
  const riskClass = (option.riskClass || 'R1') as RiskClass;

  const runId = await createAgentRun(
    'executor',
    taskDescription,
    'execute',
    riskClass,
    context.triggeredBy,
    { taskDescription, selectedOption: option.label, steps: option.steps },
  );
  await updateRunStatus(runId, 'running');

  try {
    const stepsFormatted = option.steps.map((s, i) => `${i + 1}. ${s}`).join('\n');
    const userMessage = `Task: ${taskDescription}\n\nApproved plan: ${option.label}\nRisk class: ${option.riskClass}\n\nSteps to execute:\n${stepsFormatted}\n\nExecute each step and report results.`;

    const response = await executorCall(EXECUTOR_SYSTEM_PROMPT, userMessage);

    let cleanText = response.text.trim();
    cleanText = cleanText.replace(/^```(?:json)?\s*\n?/i, '').replace(/\n?```\s*$/i, '');

    const jsonMatch = cleanText.match(/\{[\s\S]*\}/);
    if (!jsonMatch) {
      console.error('[Executor] Raw response (no JSON found):', response.text.substring(0, 500));
      throw new Error('Executor returned no valid JSON');
    }

    let parsed: ExecutorOutput;
    try {
      parsed = JSON.parse(jsonMatch[0]) as ExecutorOutput;
    } catch (parseErr) {
      console.error('[Executor] JSON parse failed:', (parseErr as Error).message);
      throw new Error('Executor returned invalid JSON');
    }
    const tokens = response.inputTokens + response.outputTokens;
    const cost = estimateCost(response.inputTokens, response.outputTokens);
    const durationMs = Date.now() - start;

    await updateRunStatus(runId, 'completed', parsed, null, response.model, tokens, cost, durationMs);

    await logAudit(
      {
        agentName: 'executor',
        actionType: 'execute_plan',
        riskClass,
        modelUsed: response.model,
        tokensUsed: tokens,
        costUsd: cost,
        durationMs,
        inputSummary: `${option.label}: ${taskDescription.substring(0, 80)}`,
        outputSummary: `${parsed.actions.length} actions, ${parsed.artifacts.length} artifacts`,
        decision: 'auto_approved',
      },
      context.traceId,
    );

    return { result: parsed, runId, tokens, cost };
  } catch (err) {
    const durationMs = Date.now() - start;
    await updateRunStatus(runId, 'failed', null, (err as Error).message, null, null, null, durationMs);

    await logAudit(
      {
        agentName: 'executor',
        actionType: 'execute_plan',
        riskClass,
        error: (err as Error).message,
        durationMs,
        inputSummary: taskDescription.substring(0, 100),
      },
      context.traceId,
    );

    throw err;
  }
}
