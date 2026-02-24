import { getNatsConnection, ensureStream, decode, publish } from '../lib/nats-client.js';
import { classify } from '../lib/ollama-client.js';
import { createInboxItem, createBrainNode } from '../lib/store.js';
import { logAudit } from '../lib/audit.js';
import { config } from '../lib/config.js';

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

    await publish(config.subjects.whatsappOutbound, {
      chatId: raw.senderId,
      text: `Captured but classification failed. Added to review queue. ID: ${inboxId.substring(0, 8)}`,
    });
    return;
  }

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

  const inboxId = await createInboxItem(
    raw.rawText,
    raw.source,
    classification,
    { senderId: raw.senderId, messageId: raw.messageId },
  );

  let brainNodeId: string | null = null;
  if (isHighConfidence) {
    brainNodeId = await createBrainNode(inboxId, raw.rawText, classification);
  }

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

export async function startInboxProcessor(): Promise<void> {
  console.log('[Inbox Processor] Starting...');

  await ensureStream('INBOX', [
    config.subjects.inboxRaw,
    config.subjects.inboxClassified,
    config.subjects.inboxReview,
  ]);

  const nc = await getNatsConnection();
  const js = nc.jetstream();

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
    console.log('[Inbox Processor] Consumer already exists or created');
  }

  const consumer = await js.consumers.get('INBOX', 'inbox-processor');

  console.log(`[Inbox Processor] Listening on ${config.subjects.inboxRaw}`);

  let running = true;
  const stop = () => { running = false; };
  process.once('SIGINT', stop);
  process.once('SIGTERM', stop);

  while (running) {
    if (nc.isClosed() || (nc as any).isDraining?.()) {
      console.log('[Inbox Processor] Connection draining/closed, exiting loop');
      break;
    }
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
      const errMsg = (err as Error).message || '';
      if (errMsg.includes('timeout') || errMsg.includes('TIMEOUT')) {
        continue;
      }
      if (errMsg.includes('DRAINING') || errMsg.includes('draining') || errMsg.includes('CLOSED')) {
        console.log('[Inbox Processor] Connection lost, exiting');
        break;
      }
      console.error('[Inbox Processor] Fetch error:', errMsg);
      await new Promise((r) => setTimeout(r, 2000));
    }
  }
}
