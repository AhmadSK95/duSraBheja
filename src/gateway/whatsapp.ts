import pkg from 'whatsapp-web.js';
const { Client, LocalAuth } = pkg;
import qrcode from 'qrcode-terminal';
import { publish, getNatsConnection, ensureStream, decode } from '../lib/nats-client.js';
import { config } from '../lib/config.js';
import { logAudit } from '../lib/audit.js';
import * as db from '../lib/db.js';

// Track the WhatsApp client globally for outbound messaging
let whatsappClient: InstanceType<typeof Client> | null = null;

function createClient(): InstanceType<typeof Client> {
  return new Client({
    authStrategy: new LocalAuth({ dataPath: '.wwebjs_auth' }),
    puppeteer: {
      headless: true,
      args: ['--no-sandbox', '--disable-setuid-sandbox'],
    },
  });
}

async function handleIncomingMessage(message: any): Promise<void> {
  // Skip group messages and status broadcasts
  if (message.from.endsWith('@g.us') || message.from === 'status@broadcast') {
    return;
  }

  const rawText = message.body?.trim();
  if (!rawText) return;

  const senderId = message.from;
  const timestamp = new Date(message.timestamp * 1000).toISOString();

  console.log(`[WhatsApp] Message from ${senderId}: ${rawText.substring(0, 80)}${rawText.length > 80 ? '...' : ''}`);

  // Check for commands
  const command = parseCommand(rawText);
  if (command) {
    await handleCommand(command, message);
    return;
  }

  // Publish raw message to NATS for processing
  await publish(config.subjects.inboxRaw, {
    rawText,
    source: 'whatsapp',
    senderId,
    timestamp,
    messageId: message.id._serialized,
  });

  await logAudit({
    agentName: 'gateway',
    actionType: 'message_received',
    riskClass: 'R0',
    inputSummary: rawText.substring(0, 100),
    metadata: { senderId, messageId: message.id._serialized },
  });
}

interface Command {
  name: string;
  args: string[];
  raw: string;
}

function parseCommand(text: string): Command | null {
  const trimmed = text.trim();

  // Shortcut commands
  if (trimmed === '?') return { name: 'today', args: [], raw: trimmed };
  if (trimmed.startsWith('!')) return { name: 'urgent', args: [trimmed.slice(1).trim()], raw: trimmed };
  if (trimmed.startsWith('+')) return { name: 'add', args: [trimmed.slice(1).trim()], raw: trimmed };

  // Keyword commands
  const keywords = ['today', 'status', 'review', 'fix', 'search', 'run', 'kill', 'help'];
  const firstWord = trimmed.split(/\s+/)[0].toLowerCase();
  if (keywords.includes(firstWord)) {
    const args = trimmed.split(/\s+/).slice(1);
    return { name: firstWord, args, raw: trimmed };
  }

  return null;
}

async function handleCommand(cmd: Command, message: any): Promise<void> {
  const chat = await message.getChat();

  switch (cmd.name) {
    case 'today': {
      const result = await db.query(
        `SELECT classified_as, count(*) as cnt FROM inbox_items
         WHERE created_at >= CURRENT_DATE GROUP BY classified_as ORDER BY cnt DESC`,
      );
      const reviewResult = await db.query(
        `SELECT count(*) as cnt FROM inbox_items WHERE status = 'review'`,
      );
      const rows = result.rows;
      const totalToday = rows.reduce((sum: number, r: any) => sum + parseInt(r.cnt), 0);
      const reviewCount = parseInt(reviewResult.rows[0]?.cnt || '0');

      let summary = `*Brain Today* (${new Date().toLocaleDateString()})\n`;
      summary += `Total captured: ${totalToday}\n`;
      rows.forEach((r: any) => {
        summary += `  ${r.classified_as}: ${r.cnt}\n`;
      });
      if (reviewCount > 0) {
        summary += `\nPending review: ${reviewCount}`;
      }
      await chat.sendMessage(summary);
      break;
    }

    case 'review': {
      const items = await db.query(
        `SELECT id, raw_text, classified_as, confidence FROM inbox_items
         WHERE status = 'review' ORDER BY created_at DESC LIMIT 5`,
      );
      if (items.rows.length === 0) {
        await chat.sendMessage('No items pending review.');
        return;
      }
      let msg = '*Review Queue*\n\n';
      items.rows.forEach((r: any, i: number) => {
        const shortId = r.id.substring(0, 8);
        msg += `${i + 1}. [${shortId}] "${r.raw_text.substring(0, 50)}..."\n`;
        msg += `   Guess: ${r.classified_as} (${(r.confidence * 100).toFixed(0)}%)\n`;
        msg += `   Fix: fix ${shortId} <category>\n\n`;
      });
      await chat.sendMessage(msg);
      break;
    }

    case 'fix': {
      const [shortId, newCategory] = cmd.args;
      if (!shortId || !newCategory) {
        await chat.sendMessage('Usage: fix <id> <category>\nCategories: idea, task, note, question, link');
        return;
      }
      const validCategories = ['idea', 'task', 'note', 'question', 'link'];
      if (!validCategories.includes(newCategory)) {
        await chat.sendMessage(`Invalid category. Use: ${validCategories.join(', ')}`);
        return;
      }
      // Find the item by short ID prefix
      const found = await db.query(
        `SELECT id, classified_as FROM inbox_items WHERE id::text LIKE $1 LIMIT 1`,
        [`${shortId}%`],
      );
      if (found.rows.length === 0) {
        await chat.sendMessage(`Item ${shortId} not found.`);
        return;
      }
      const { applyCorrection } = await import('../lib/store.js');
      await applyCorrection(found.rows[0].id, 'classified_as', found.rows[0].classified_as, newCategory);
      await logAudit({
        agentName: 'user',
        actionType: 'correction',
        inputSummary: `${shortId}: ${found.rows[0].classified_as} → ${newCategory}`,
      });
      await chat.sendMessage(`Fixed. ${shortId} → ${newCategory}`);
      break;
    }

    case 'search': {
      const searchText = cmd.args.join(' ');
      if (!searchText) {
        await chat.sendMessage('Usage: search <query>');
        return;
      }
      const results = await db.query(
        `SELECT id, title, node_type, priority FROM brain_nodes
         WHERE title ILIKE $1 OR content ILIKE $1
         ORDER BY created_at DESC LIMIT 5`,
        [`%${searchText}%`],
      );
      if (results.rows.length === 0) {
        await chat.sendMessage(`No results for "${searchText}"`);
        return;
      }
      let msg = `*Search: ${searchText}*\n\n`;
      results.rows.forEach((r: any, i: number) => {
        msg += `${i + 1}. [${r.node_type}] ${r.title} (${r.priority})\n`;
      });
      await chat.sendMessage(msg);
      break;
    }

    case 'help': {
      await chat.sendMessage(
        '*duSraBheja Commands*\n\n' +
        '? — Today\'s brain summary\n' +
        '+ <text> — Quick add (same as sending normally)\n' +
        '! <text> — Urgent capture\n' +
        'today — Daily status\n' +
        'review — Show review queue\n' +
        'fix <id> <category> — Correct classification\n' +
        'search <query> — Search brain\n' +
        'status — System status\n' +
        'help — This message',
      );
      break;
    }

    case 'urgent': {
      // Treat as high-priority capture
      await publish(config.subjects.inboxRaw, {
        rawText: cmd.args.join(' '),
        source: 'whatsapp',
        senderId: message.from,
        timestamp: new Date().toISOString(),
        messageId: message.id._serialized,
        urgent: true,
      });
      break;
    }

    case 'status': {
      const serviceStatus = [];
      try { await db.query('SELECT 1'); serviceStatus.push('DB: up'); } catch { serviceStatus.push('DB: down'); }
      serviceStatus.push('WhatsApp: up'); // We're running
      await chat.sendMessage(`*System Status*\n${serviceStatus.join('\n')}`);
      break;
    }

    default:
      await chat.sendMessage(`Unknown command: ${cmd.name}. Send "help" for available commands.`);
  }
}

// Outbound message handler — listens on NATS for messages to send
async function startOutboundListener(): Promise<void> {
  const nc = await getNatsConnection();
  const sub = nc.subscribe(config.subjects.whatsappOutbound);

  console.log(`[WhatsApp] Listening for outbound messages on ${config.subjects.whatsappOutbound}`);

  for await (const msg of sub) {
    try {
      const data = JSON.parse(decode(msg.data));
      const { chatId, text } = data;

      if (whatsappClient && chatId && text) {
        await whatsappClient.sendMessage(chatId, text);
        console.log(`[WhatsApp] Sent reply to ${chatId}: ${text.substring(0, 60)}...`);
      }
    } catch (err) {
      console.error('[WhatsApp] Outbound error:', (err as Error).message);
    }
  }
}

async function main(): Promise<void> {
  console.log('[WhatsApp] Starting gateway...');

  // Ensure NATS streams exist
  await ensureStream('INBOX', [
    config.subjects.inboxRaw,
    config.subjects.inboxClassified,
    config.subjects.inboxReview,
  ]);
  await ensureStream('WHATSAPP', [config.subjects.whatsappOutbound]);

  const client = createClient();
  whatsappClient = client;

  client.on('qr', (qr: string) => {
    console.log('[WhatsApp] Scan this QR code:');
    qrcode.generate(qr, { small: true });
  });

  client.on('ready', () => {
    console.log('[WhatsApp] Client ready and connected');
    startOutboundListener();
  });

  client.on('authenticated', () => {
    console.log('[WhatsApp] Authenticated');
  });

  client.on('auth_failure', (msg: string) => {
    console.error('[WhatsApp] Auth failure:', msg);
  });

  client.on('disconnected', (reason: string) => {
    console.warn('[WhatsApp] Disconnected:', reason);
  });

  client.on('message', handleIncomingMessage);

  await client.initialize();

  // Graceful shutdown
  const shutdown = async () => {
    console.log('[WhatsApp] Shutting down...');
    await client.destroy();
    const { shutdown: natsShutdown } = await import('../lib/nats-client.js');
    await natsShutdown();
    const { shutdown: dbShutdown } = await import('../lib/db.js');
    await dbShutdown();
    process.exit(0);
  };

  process.on('SIGINT', shutdown);
  process.on('SIGTERM', shutdown);
}

main().catch((err) => {
  console.error('[WhatsApp] Fatal error:', err);
  process.exit(1);
});
