import { startGateway } from './gateway/whatsapp.js';
import { startInboxProcessor } from './workers/inbox-processor.js';
import { startResponder } from './workers/responder.js';
import { shutdown as natsShutdown } from './lib/nats-client.js';
import { shutdown as dbShutdown } from './lib/db.js';

async function main(): Promise<void> {
  console.log('');
  console.log('═══════════════════════════════════════════');
  console.log('  duSraBheja — Solo AI Command Center');
  console.log('═══════════════════════════════════════════');
  console.log('');

  // Start responder first (lightweight, just NATS subscriptions)
  await startResponder();

  // Start inbox processor (NATS consumer loop)
  startInboxProcessor().catch((err) => {
    console.error('[Main] Inbox Processor error:', err);
  });

  // Start WhatsApp gateway (heaviest — launches Chromium)
  await startGateway();

  console.log('[Main] All services running in single process.');
}

const shutdown = async () => {
  console.log('\n[Main] Shutting down...');
  await natsShutdown();
  await dbShutdown();
  process.exit(0);
};

process.on('SIGINT', shutdown);
process.on('SIGTERM', shutdown);

main().catch((err) => {
  console.error('[Main] Fatal error:', err);
  process.exit(1);
});
