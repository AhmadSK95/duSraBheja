import { Client, Connection } from '@temporalio/client';
import { config } from '../lib/config.js';

async function main(): Promise<void> {
  const connection = await Connection.connect({
    address: config.temporal.address,
  });
  const client = new Client({ connection });

  // Create or update the daily summary schedule
  const scheduleId = 'daily-summary';

  try {
    const handle = client.schedule.getHandle(scheduleId);
    await handle.describe();
    console.log(`[Schedule] Schedule '${scheduleId}' already exists. Updating...`);
    await handle.delete();
  } catch {
    // Schedule doesn't exist, will create
  }

  await client.schedule.create({
    scheduleId,
    spec: {
      calendars: [
        {
          hour: 8,
          minute: 0,
          comment: 'Daily summary at 8am',
        },
      ],
    },
    action: {
      type: 'startWorkflow',
      workflowType: 'dailySummaryWorkflow',
      taskQueue: config.temporal.taskQueue,
      workflowId: `daily-summary-${new Date().toISOString().split('T')[0]}`,
    },
  });

  console.log(`[Schedule] Daily summary scheduled for 8:00 AM`);
  console.log(`[Schedule] View at http://localhost:8233/schedules/${scheduleId}`);

  await connection.close();
}

main().catch((err) => {
  console.error('[Schedule] Error:', err);
  process.exit(1);
});
