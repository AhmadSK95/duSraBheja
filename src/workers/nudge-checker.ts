import { config } from '../lib/config.js';
import { query } from '../lib/db.js';
import { publish } from '../lib/nats-client.js';
import { getStaleItems } from '../lib/project-store.js';
import { logAudit } from '../lib/audit.js';

let checkTimer: ReturnType<typeof setInterval> | null = null;

async function getUserChatId(): Promise<string | null> {
  const result = await query<any>(
    `SELECT source_metadata->>'senderId' as sender_id FROM inbox_items
     WHERE source = 'whatsapp' AND source_metadata->>'senderId' IS NOT NULL
     ORDER BY created_at DESC LIMIT 1`,
  );
  return result.rows[0]?.sender_id || null;
}

async function runNudgeCheck(): Promise<void> {
  const start = Date.now();
  try {
    const staleItems = await getStaleItems(config.nudge.staleDaysThreshold);

    if (staleItems.length === 0) {
      console.log('[Nudge Checker] No stale items found');
      return;
    }

    // Build message
    let msg = `*Nudge: ${staleItems.length} stale item(s)*\n\n`;
    for (const item of staleItems.slice(0, 10)) {
      const daysAgo = Math.floor((Date.now() - item.updatedAt.getTime()) / (1000 * 60 * 60 * 24));
      msg += `[${item.nodeType}] ${item.title} (${daysAgo}d)`;
      if (item.projectName) msg += ` — ${item.projectName}`;
      msg += '\n';
    }
    if (staleItems.length > 10) {
      msg += `\n...and ${staleItems.length - 10} more. Send "stale" for full list.`;
    }

    // Create nudge records
    for (const item of staleItems) {
      await query(
        `INSERT INTO nudges (nudge_type, target_node_id, message, status)
         VALUES ('stale_reminder', $1, $2, 'pending')`,
        [item.id, `Stale: ${item.title}`],
      );
    }

    // Send via WhatsApp
    const chatId = await getUserChatId();
    if (chatId) {
      await publish(config.subjects.whatsappOutbound, { chatId, text: msg });

      // Mark nudges as sent
      await query(
        `UPDATE nudges SET status = 'sent', sent_at = NOW()
         WHERE status = 'pending' AND nudge_type = 'stale_reminder'`,
      );
    }

    await logAudit({
      agentName: 'nudge-checker',
      actionType: 'stale_check',
      riskClass: 'R0',
      outputSummary: `Found ${staleItems.length} stale items`,
      durationMs: Date.now() - start,
    });

    console.log(`[Nudge Checker] Found ${staleItems.length} stale item(s), nudge sent`);
  } catch (err) {
    console.error('[Nudge Checker] Check failed:', (err as Error).message);
    await logAudit({
      agentName: 'nudge-checker',
      actionType: 'stale_check',
      riskClass: 'R0',
      error: (err as Error).message,
      durationMs: Date.now() - start,
    });
  }
}

export async function startNudgeChecker(): Promise<void> {
  const intervalMs = config.nudge.checkIntervalHours * 60 * 60 * 1000;
  console.log(`[Nudge Checker] Starting (interval: ${config.nudge.checkIntervalHours}h, threshold: ${config.nudge.staleDaysThreshold}d)`);

  // First check after 60s
  setTimeout(() => {
    runNudgeCheck().catch((err) => console.error('[Nudge Checker] Initial check error:', err.message));
  }, 60000);

  // Recurring checks
  checkTimer = setInterval(() => {
    runNudgeCheck().catch((err) => console.error('[Nudge Checker] Check error:', err.message));
  }, intervalMs);

  // Don't block process exit
  if (checkTimer.unref) checkTimer.unref();
}

export function stopNudgeChecker(): void {
  if (checkTimer) {
    clearInterval(checkTimer);
    checkTimer = null;
    console.log('[Nudge Checker] Stopped');
  }
}
