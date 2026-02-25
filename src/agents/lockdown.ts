import { logAudit } from '../lib/audit.js';
import { cancelAllRuns } from './agent-store.js';

// In-memory flag — resets on restart (safe default: not locked)
let lockdownActive = false;

export function isLockdown(): boolean {
  return lockdownActive;
}

export async function activateKillSwitch(traceId: string): Promise<number> {
  lockdownActive = true;

  // Cancel all active agent runs
  const cancelledCount = await cancelAllRuns();

  await logAudit(
    {
      agentName: 'system',
      actionType: 'kill_switch',
      riskClass: 'R0',
      outputSummary: `Lockdown activated, ${cancelledCount} runs cancelled`,
      decision: 'kill_switch',
    },
    traceId,
  );

  console.log(`[Lockdown] ACTIVATED — ${cancelledCount} runs cancelled`);
  return cancelledCount;
}

export async function resumeFromLockdown(traceId: string): Promise<void> {
  lockdownActive = false;

  await logAudit(
    {
      agentName: 'system',
      actionType: 'resume',
      riskClass: 'R0',
      outputSummary: 'Lockdown deactivated',
      decision: 'resume',
    },
    traceId,
  );

  console.log('[Lockdown] DEACTIVATED — operations resumed');
}
