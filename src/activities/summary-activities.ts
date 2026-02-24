import { query } from '../lib/db.js';
import { summarize } from '../lib/ollama-client.js';
import { publish } from '../lib/nats-client.js';
import { logAudit } from '../lib/audit.js';
import { config } from '../lib/config.js';

export interface DailySummaryData {
  totalItems: number;
  byCategory: Record<string, number>;
  highPriority: Array<{ id: string; title: string; type: string }>;
  pendingReview: number;
  recentItems: Array<{ rawText: string; classifiedAs: string; priority: string }>;
}

export async function fetchDailySummaryData(): Promise<DailySummaryData> {
  // Count by category
  const categoryResult = await query(
    `SELECT classified_as, count(*) as cnt FROM inbox_items
     WHERE created_at >= CURRENT_DATE - INTERVAL '1 day'
     AND classified_as IS NOT NULL
     GROUP BY classified_as`,
  );

  const byCategory: Record<string, number> = {};
  let totalItems = 0;
  for (const row of categoryResult.rows) {
    byCategory[row.classified_as] = parseInt(row.cnt);
    totalItems += parseInt(row.cnt);
  }

  // High priority items
  const highPriResult = await query(
    `SELECT bn.id, bn.title, bn.node_type FROM brain_nodes bn
     WHERE bn.created_at >= CURRENT_DATE - INTERVAL '1 day'
     AND bn.priority IN ('high', 'urgent')
     ORDER BY bn.created_at DESC LIMIT 10`,
  );

  // Pending review count
  const reviewResult = await query(
    `SELECT count(*) as cnt FROM inbox_items WHERE status = 'review'`,
  );

  // Recent items for context
  const recentResult = await query(
    `SELECT raw_text, classified_as, priority FROM inbox_items
     WHERE created_at >= CURRENT_DATE - INTERVAL '1 day'
     AND classified_as IS NOT NULL
     ORDER BY created_at DESC LIMIT 20`,
  );

  return {
    totalItems,
    byCategory,
    highPriority: highPriResult.rows.map((r: any) => ({
      id: r.id,
      title: r.title,
      type: r.node_type,
    })),
    pendingReview: parseInt(reviewResult.rows[0]?.cnt || '0'),
    recentItems: recentResult.rows.map((r: any) => ({
      rawText: r.raw_text,
      classifiedAs: r.classified_as,
      priority: r.priority,
    })),
  };
}

export async function generateSummaryText(data: DailySummaryData): Promise<string> {
  const start = Date.now();

  // Build context for Ollama
  const context = [
    `Items captured: ${data.totalItems}`,
    `Breakdown: ${Object.entries(data.byCategory).map(([k, v]) => `${k}: ${v}`).join(', ')}`,
    `Pending review: ${data.pendingReview}`,
  ];

  if (data.highPriority.length > 0) {
    context.push(`High priority items:`);
    data.highPriority.forEach((item) => {
      context.push(`  - [${item.type}] ${item.title}`);
    });
  }

  if (data.recentItems.length > 0) {
    context.push(`\nRecent items:`);
    data.recentItems.slice(0, 10).forEach((item) => {
      context.push(`  - [${item.classifiedAs}/${item.priority}] ${item.rawText.substring(0, 80)}`);
    });
  }

  const raw = await summarize(context.join('\n'));
  const durationMs = Date.now() - start;

  // Format for WhatsApp
  const header = `*Brain Daily Summary* — ${new Date().toLocaleDateString('en-US', { weekday: 'long', month: 'short', day: 'numeric' })}`;
  const stats = `Captured: ${data.totalItems} | Review: ${data.pendingReview}`;
  const summary = `${header}\n${stats}\n\n${raw}`;

  await logAudit({
    agentName: 'narrator',
    actionType: 'daily_summary',
    riskClass: 'R0',
    toolName: 'ollama',
    modelUsed: config.ollama.summaryModel,
    durationMs,
    outputSummary: summary.substring(0, 200),
  });

  return summary;
}

export async function sendWhatsAppSummary(text: string): Promise<void> {
  // Get the user's chat ID from the most recent inbox item
  const result = await query(
    `SELECT source_metadata->>'senderId' as sender_id FROM inbox_items
     WHERE source = 'whatsapp' AND source_metadata->>'senderId' IS NOT NULL
     ORDER BY created_at DESC LIMIT 1`,
  );

  if (result.rows.length === 0) {
    console.warn('[Summary] No WhatsApp sender found for summary delivery');
    return;
  }

  const chatId = result.rows[0].sender_id;
  await publish(config.subjects.whatsappOutbound, { chatId, text });

  console.log(`[Summary] Daily summary sent to ${chatId}`);
}
