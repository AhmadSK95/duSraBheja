import { publish } from '../lib/nats-client.js';
import { config } from '../lib/config.js';
import type {
  AgentGraphState,
  PlannerOutput,
  CriticOutput,
  ExecutorOutput,
} from './types.js';
import type { AgentRun } from './agent-store.js';

export function formatPlanForApproval(
  plan: PlannerOutput,
  review: CriticOutput,
  task: string,
  runId: string,
): string {
  let msg = `*Agent Plan* | ${task}\n\n`;

  for (let i = 0; i < plan.options.length; i++) {
    const opt = plan.options[i];
    const rec = i === plan.recommendedIndex ? ' [RECOMMENDED]' : '';
    msg += `*Option ${i + 1}: ${opt.label}*${rec}\n`;
    msg += `Risk: ${opt.riskClass} | Est: $${opt.estimatedCostUsd.toFixed(3)}\n`;
    for (const step of opt.steps) {
      msg += `  - ${step}\n`;
    }
    msg += '\n';
  }

  msg += `*Critic Review* (${review.score}/10)\n`;
  if (review.issues.length > 0) {
    msg += `Issues: ${review.issues.join('; ')}\n`;
  }
  if (review.suggestions.length > 0) {
    msg += `Suggestions: ${review.suggestions.join('; ')}\n`;
  }
  msg += `Verdict: ${review.approved ? 'APPROVED' : 'NEEDS REVIEW'}\n`;
  msg += `\nReply *approve* or *deny*`;

  return msg;
}

export function formatExecutionResult(
  result: ExecutorOutput,
  task: string,
  state: AgentGraphState,
): string {
  let msg = `*Executed* | ${task}\n\n`;
  msg += result.result + '\n';

  if (result.actions.length > 0) {
    msg += '\n*Actions:*\n';
    for (const action of result.actions) {
      msg += `  - ${action}\n`;
    }
  }

  if (result.artifacts.length > 0) {
    msg += '\n*Artifacts:*\n';
    for (const artifact of result.artifacts) {
      msg += `  - ${artifact}\n`;
    }
  }

  msg += `\nTokens: ${state.totalTokens} | Cost: $${state.totalCost.toFixed(3)} | Time: ${(state.totalDurationMs / 1000).toFixed(1)}s`;

  return msg;
}

export function formatDenial(
  task: string,
  reason: string,
  state: AgentGraphState,
): string {
  let msg = `*Denied* | ${task}\n`;
  msg += `Reason: ${reason}\n`;
  msg += `Tokens used: ${state.totalTokens} | Cost: $${state.totalCost.toFixed(3)}`;
  return msg;
}

export function formatKillConfirmation(cancelledCount: number): string {
  return `*LOCKDOWN ACTIVE*\nAll agent operations suspended.\n${cancelledCount} running task(s) cancelled.\nSend *resume* to exit lockdown.`;
}

export function formatResumeConfirmation(): string {
  return `*LOCKDOWN LIFTED*\nAgent operations resumed.\nAll systems operational.`;
}

export function formatAgentStatus(
  activeRuns: AgentRun[],
  recentRuns: AgentRun[],
  isLockdown: boolean,
): string {
  let msg = `*Agent Status*${isLockdown ? ' [LOCKDOWN]' : ''}\n\n`;

  if (activeRuns.length > 0) {
    msg += '*Active:*\n';
    for (const run of activeRuns) {
      const shortId = run.id.substring(0, 8);
      msg += `  [${run.status}] ${run.agentName}: ${run.taskDescription.substring(0, 40)} (${shortId})\n`;
    }
    msg += '\n';
  } else {
    msg += 'No active agents.\n\n';
  }

  if (recentRuns.length > 0) {
    msg += '*Recent:*\n';
    for (const run of recentRuns) {
      const shortId = run.id.substring(0, 8);
      const cost = run.costUsd ? ` $${run.costUsd.toFixed(3)}` : '';
      const dur = run.durationMs ? ` ${(run.durationMs / 1000).toFixed(1)}s` : '';
      msg += `  [${run.status}] ${run.agentName}: ${run.taskDescription.substring(0, 40)}${cost}${dur} (${shortId})\n`;
    }
  }

  return msg;
}

export function formatError(task: string, error: string): string {
  return `*Agent Error* | ${task}\n${error}`;
}

export async function sendToWhatsApp(chatId: string, message: string): Promise<void> {
  await publish(config.subjects.whatsappOutbound, { chatId, text: message });
}
