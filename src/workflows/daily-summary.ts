import { proxyActivities } from '@temporalio/workflow';
import type * as activities from '../activities/summary-activities.js';

const { fetchDailySummaryData, generateSummaryText, sendWhatsAppSummary } = proxyActivities<typeof activities>({
  startToCloseTimeout: '2 minutes',
  retry: {
    maximumAttempts: 3,
  },
});

export async function dailySummaryWorkflow(): Promise<string> {
  // Step 1: Fetch today's data from Postgres
  const data = await fetchDailySummaryData();

  if (data.totalItems === 0) {
    const msg = 'Good morning! No items captured yesterday. Send me anything to get started.';
    await sendWhatsAppSummary(msg);
    return msg;
  }

  // Step 2: Generate summary via Ollama
  const summaryText = await generateSummaryText(data);

  // Step 3: Send to WhatsApp
  await sendWhatsAppSummary(summaryText);

  return summaryText;
}
