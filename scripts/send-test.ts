import { publish, ensureStream, shutdown } from '../src/lib/nats-client.js';
import { config } from '../src/lib/config.js';

async function main() {
  await ensureStream('INBOX', [
    config.subjects.inboxRaw,
    config.subjects.inboxClassified,
    config.subjects.inboxReview,
  ]);

  const testMessages = [
    'Build a habit tracker app with streaks and notifications',
    'Fix the auth bug on the dashboard - users getting logged out randomly',
    'The new OpenAI o3 model supports structured outputs natively now',
  ];

  for (const msg of testMessages) {
    await publish(config.subjects.inboxRaw, {
      rawText: msg,
      source: 'whatsapp',
      senderId: 'test@cli',
      timestamp: new Date().toISOString(),
      messageId: 'test-' + Date.now(),
    });
    console.log('Published:', msg);
    // Small delay between messages
    await new Promise((r) => setTimeout(r, 500));
  }

  console.log('\nDone. Check your terminal running npm run dev for processing logs.');
  await shutdown();
}

main().catch(console.error);
