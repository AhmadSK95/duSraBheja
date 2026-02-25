import { config } from '../lib/config.js';
import { syncAllRepos } from '../lib/github-client.js';
import { logAudit } from '../lib/audit.js';

let pollTimer: ReturnType<typeof setInterval> | null = null;

async function runSync(): Promise<void> {
  const start = Date.now();
  try {
    const count = await syncAllRepos();
    console.log(`[GitHub Poller] Synced ${count} repo(s) in ${Date.now() - start}ms`);
    await logAudit({
      agentName: 'github-poller',
      actionType: 'poll_cycle',
      riskClass: 'R0',
      outputSummary: `Synced ${count} repos`,
      durationMs: Date.now() - start,
    });
  } catch (err) {
    console.error('[GitHub Poller] Sync cycle failed:', (err as Error).message);
    await logAudit({
      agentName: 'github-poller',
      actionType: 'poll_cycle',
      riskClass: 'R0',
      error: (err as Error).message,
      durationMs: Date.now() - start,
    });
  }
}

export async function startGitHubPoller(): Promise<void> {
  if (!config.github.token) {
    console.log('[GitHub Poller] GITHUB_TOKEN not set — poller disabled');
    return;
  }

  const intervalMs = config.github.pollIntervalMinutes * 60 * 1000;
  console.log(`[GitHub Poller] Starting (interval: ${config.github.pollIntervalMinutes}min)`);

  // Initial sync after a short delay to let other services start
  setTimeout(() => {
    runSync().catch((err) => console.error('[GitHub Poller] Initial sync error:', err.message));
  }, 5000);

  // Recurring sync
  pollTimer = setInterval(() => {
    runSync().catch((err) => console.error('[GitHub Poller] Poll error:', err.message));
  }, intervalMs);

  // Don't block process exit
  if (pollTimer.unref) pollTimer.unref();
}

export function stopGitHubPoller(): void {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
    console.log('[GitHub Poller] Stopped');
  }
}
