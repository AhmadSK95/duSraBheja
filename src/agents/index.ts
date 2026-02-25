import { loadPolicies } from './sentinel.js';
import { startApprovalPoller, stopApprovalPoller } from './approval-poller.js';

export { runAgentChain } from './graph.js';
export { activateKillSwitch, resumeFromLockdown, isLockdown } from './lockdown.js';
export { getActiveRuns, getRecentRuns, getPendingApproval, resolveApproval } from './agent-store.js';
export { formatAgentStatus, formatKillConfirmation, formatResumeConfirmation } from './narrator.js';
export { storyboardFromText, storyboardFromTasks, storyboardFromIdeas } from './storyboard.js';
export type { AgentGraphState, AgentContext } from './types.js';

export async function startAgentSubsystem(): Promise<void> {
  console.log('[Agents] Starting agent subsystem...');

  // Load safety policies into cache
  try {
    const policies = await loadPolicies();
    console.log(`[Agents] Loaded ${policies.length} policy rules`);
  } catch (err) {
    console.error('[Agents] Failed to load policies (FAIL-CLOSED):', (err as Error).message);
  }

  // Start approval timeout poller
  startApprovalPoller();

  console.log('[Agents] Agent subsystem ready');
}

export function stopAgentSubsystem(): void {
  stopApprovalPoller();
  console.log('[Agents] Agent subsystem stopped');
}
