import { getNatsConnection, ensureStream, decode, publish } from '../lib/nats-client.js';
import { config } from '../lib/config.js';

interface ClassifiedEvent {
  inboxId: string;
  brainNodeId: string | null;
  senderId: string;
  classification: {
    category: string;
    confidence: number;
    priority: string;
    nextAction: string;
    summary: string;
  };
  isHighConfidence: boolean;
}

function formatConfirmation(event: ClassifiedEvent): string {
  const { classification, inboxId, isHighConfidence } = event;
  const shortId = inboxId.substring(0, 8);
  const conf = (classification.confidence * 100).toFixed(0);

  if (isHighConfidence) {
    let msg = `Captured. *${classification.category}* (${conf}%)`;
    if (classification.priority !== 'medium') {
      msg += ` | Priority: ${classification.priority}`;
    }
    if (classification.nextAction) {
      msg += `\nNext: ${classification.nextAction}`;
    }
    return msg;
  } else {
    return (
      `Captured but needs review (${conf}% confidence)\n` +
      `Best guess: *${classification.category}*\n` +
      `Reply: fix ${shortId} <idea|task|note|question|link>`
    );
  }
}

export async function startResponder(): Promise<void> {
  console.log('[Responder] Starting...');

  await ensureStream('INBOX', [
    config.subjects.inboxRaw,
    config.subjects.inboxClassified,
    config.subjects.inboxReview,
  ]);
  await ensureStream('WHATSAPP', [config.subjects.whatsappOutbound]);

  const nc = await getNatsConnection();

  const subjects = [config.subjects.inboxClassified, config.subjects.inboxReview];

  for (const subject of subjects) {
    const sub = nc.subscribe(subject);
    console.log(`[Responder] Listening on ${subject}`);

    (async () => {
      for await (const msg of sub) {
        try {
          const event: ClassifiedEvent = JSON.parse(decode(msg.data));
          const text = formatConfirmation(event);

          await publish(config.subjects.whatsappOutbound, {
            chatId: event.senderId,
            text,
          });

          console.log(`[Responder] Sent confirmation for ${event.inboxId.substring(0, 8)} to ${event.senderId}`);
        } catch (err) {
          console.error('[Responder] Error:', (err as Error).message);
        }
      }
    })();
  }

  console.log('[Responder] Ready');
}
