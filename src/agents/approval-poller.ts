import { config } from '../lib/config.js';
import { timeoutStaleApprovals } from './agent-store.js';

let pollTimer: ReturnType<typeof setInterval> | null = null;

async function checkApprovalTimeouts(): Promise<void> {
  try {
    const timedOut = await timeoutStaleApprovals(config.agents.approvalTimeoutMinutes);
    if (timedOut > 0) {
      console.log(`[Approval Poller] Timed out ${timedOut} stale approval(s)`);
    }
  } catch (err) {
    console.error('[Approval Poller] Check failed:', (err as Error).message);
  }
}

export function startApprovalPoller(): void {
  console.log(`[Approval Poller] Starting (timeout: ${config.agents.approvalTimeoutMinutes}min)`);

  // Check every 60 seconds
  pollTimer = setInterval(() => {
    checkApprovalTimeouts().catch((err) =>
      console.error('[Approval Poller] Error:', err.message),
    );
  }, 60_000);

  // Don't block process exit
  if (pollTimer.unref) pollTimer.unref();
}

export function stopApprovalPoller(): void {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
    console.log('[Approval Poller] Stopped');
  }
}
