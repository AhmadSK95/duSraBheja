import { Worker } from '@temporalio/worker';
import { config } from '../lib/config.js';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

async function main(): Promise<void> {
  console.log('[Temporal Worker] Starting...');

  const worker = await Worker.create({
    workflowsPath: path.resolve(__dirname, '../workflows/daily-summary.ts'),
    activities: await import('../activities/summary-activities.js'),
    taskQueue: config.temporal.taskQueue,
    connection: {
      address: config.temporal.address,
    } as any,
  });

  console.log(`[Temporal Worker] Listening on task queue: ${config.temporal.taskQueue}`);

  await worker.run();
}

main().catch((err) => {
  console.error('[Temporal Worker] Fatal error:', err);
  process.exit(1);
});
