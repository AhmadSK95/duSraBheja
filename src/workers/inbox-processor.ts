import { getNatsConnection, ensureStream, decode, publish } from '../lib/nats-client.js';
import { classify } from '../lib/ollama-client.js';
import { createInboxItem, createBrainNode } from '../lib/store.js';
import { logAudit } from '../lib/audit.js';
import { config } from '../lib/config.js';
import * as db from '../lib/db.js';

interface RawMessage {
  rawText: string;
  source: string;
  senderId: string;
  timestamp: string;
  messageId: string;
  urgent?: boolean;
}

async function processMessage(raw: RawMessage): Promise<void> {
  const start = Date.now();
  const traceId = crypto.randomUUID();

  console.log(`[Inbox] Processing: "${raw.rawText.substring(0, 60)}..."`);

  let classification;
  try {
    classification = await classify(raw.rawText);
  } catch (err) {
    console.error('[Inbox] Classification failed:', (err as Error).message);
    // Store as unclassified with review status
    const inboxId = await createInboxItem(raw.rawText, raw.source, null, {
      senderId: raw.senderId,
      messageId: raw.messageId,
    });
    await logAudit({
      agentName: 'classifier',
      actionType: 'classify_failed',
      riskClass: 'R0',
      inputSummary: raw.rawText.substring(0, 100),
      error: (err as Error).message,
      durationMs: Date.now() - start,
    }, traceId);

    // Still notify user
    await publish(config.subjects.whatsappOutbound, {
      chatId: raw.senderId,
      text: `Captured but classification failed. Added to review queue. ID: ${inboxId.substring(0, 8)}`,
    });
    return;
  }

  // Override priority if urgent
  if (raw.urgent) {
    classification.priority = 'urgent';
  }

  const durationMs = Date.now() - start;
  const isHighConfidence = classification.confidence >= config.confidenceThreshold;

  console.log(
    `[Inbox] Classified as: ${classification.category} ` +
    `(confidence: ${(classification.confidence * 100).toFixed(0)}%, ` +
    `threshold: ${isHighConfidence ? 'PASS' : 'REVIEW'}) ` +
    `[${durationMs}ms]`,
  );

  // Store inbox item
  const inboxId = await createInboxItem(
    raw.rawText,
    raw.source,
    classification,
    { senderId: raw.senderId, messageId: raw.messageId },
  );

  // If high confidence, also create a brain node
  let brainNodeId: string | null = null;
  if (isHighConfidence) {
    brainNodeId = await createBrainNode(inboxId, raw.rawText, classification);
  }

  // Audit the classification
  await logAudit({
    agentName: 'classifier',
    actionType: 'classify',
    riskClass: 'R0',
    toolName: 'ollama',
    inputSummary: raw.rawText.substring(0, 100),
    outputSummary: `${classification.category} (${(classification.confidence * 100).toFixed(0)}%)`,
    decision: isHighConfidence ? 'auto_approved' : 'escalated',
    modelUsed: config.ollama.classifyModel,
    durationMs,
    metadata: {
      inboxId,
      brainNodeId,
      classification,
    },
  }, traceId);

  // Publish result for WhatsApp responder
  const subject = isHighConfidence
    ? config.subjects.inboxClassified
    : config.subjects.inboxReview;

  await publish(subject, {
    inboxId,
    brainNodeId,
    senderId: raw.senderId,
    classification,
    isHighConfidence,
  });
}

async function main(): Promise<void> {
  console.log('[Inbox Processor] Starting...');

  // Ensure streams exist
  await ensureStream('INBOX', [
    config.subjects.inboxRaw,
    config.subjects.inboxClassified,
    config.subjects.inboxReview,
  ]);

  const nc = await getNatsConnection();
  const js = nc.jetstream();

  // Create a durable consumer for inbox.raw
  const jsm = await nc.jetstreamManager();
  try {
    await jsm.consumers.add('INBOX', {
      durable_name: 'inbox-processor',
      filter_subject: config.subjects.inboxRaw,
      ack_policy: 'explicit' as any,
      deliver_policy: 'all' as any,
      max_deliver: 3,
    });
    console.log('[Inbox Processor] Consumer created/verified');
  } catch (err) {
    // Consumer might already exist
    console.log('[Inbox Processor] Consumer already exists or created');
  }

  const consumer = await js.consumers.get('INBOX', 'inbox-processor');

  console.log(`[Inbox Processor] Listening on ${config.subjects.inboxRaw}`);

  // Process messages
  while (true) {
    try {
      const messages = await consumer.fetch({ max_messages: 1, expires: 5000 });

      for await (const msg of messages) {
        try {
          const raw: RawMessage = JSON.parse(decode(msg.data));
          await processMessage(raw);
          msg.ack();
        } catch (err) {
          console.error('[Inbox Processor] Message processing error:', (err as Error).message);
          msg.nak();
        }
      }
    } catch (err) {
      // Timeout is expected when no messages
      if (!(err as Error).message?.includes('timeout')) {
        console.error('[Inbox Processor] Fetch error:', (err as Error).message);
      }
    }
  }
}

// Graceful shutdown
const shutdown = async () => {
  console.log('[Inbox Processor] Shutting down...');
  const { shutdown: natsShutdown } = await import('../lib/nats-client.js');
  await natsShutdown();
  const { shutdown: dbShutdown } = await import('../lib/db.js');
  await dbShutdown();
  process.exit(0);
};

process.on('SIGINT', shutdown);
process.on('SIGTERM', shutdown);

main().catch((err) => {
  console.error('[Inbox Processor] Fatal error:', err);
  process.exit(1);
});
