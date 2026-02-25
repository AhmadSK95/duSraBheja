import pkg from 'whatsapp-web.js';
const { Client, LocalAuth } = pkg;
import qrcode from 'qrcode-terminal';
import { publish, getNatsConnection, ensureStream, decode } from '../lib/nats-client.js';
import { config } from '../lib/config.js';
import { logAudit } from '../lib/audit.js';
import * as db from '../lib/db.js';
import {
  listActiveProjects,
  getProjectByName,
  createProject,
  archiveProject,
  updateProjectRepo,
  updateProjectPath,
  getProjectDashboard,
  createTask,
  listProjectTasks,
  completeTask,
  archiveTask,
  getStaleItems,
} from '../lib/project-store.js';
import {
  getLatestRepoStatus,
  formatRepoStatus,
  formatAllReposSummary,
  syncAllRepos,
  type RepoSnapshot,
} from '../lib/github-client.js';
import {
  runAgentChain,
  activateKillSwitch,
  resumeFromLockdown,
  isLockdown,
  getActiveRuns,
  getRecentRuns,
  getPendingApproval,
  resolveApproval,
  formatAgentStatus,
  formatKillConfirmation,
  formatResumeConfirmation,
  storyboardFromText,
  storyboardFromTasks,
  storyboardFromIdeas,
} from '../agents/index.js';
import { v4 as uuidv4 } from 'uuid';

// Track the WhatsApp client globally for outbound messaging
let whatsappClient: InstanceType<typeof Client> | null = null;
// The user's own chat ID (messages to yourself). Set on first ready.
let myChatId: string | null = null;
// Track recently sent bot messages to avoid re-processing our own replies
const recentBotMessages = new Set<string>();

function createClient(): InstanceType<typeof Client> {
  return new Client({
    authStrategy: new LocalAuth({ dataPath: '.wwebjs_auth' }),
    puppeteer: {
      headless: true,
      args: [
        '--no-sandbox',
        '--disable-setuid-sandbox',
        '--disable-dev-shm-usage',
        '--disable-gpu',
        '--disable-extensions',
        '--disable-background-networking',
        '--disable-default-apps',
        '--disable-sync',
        '--disable-translate',
        '--metrics-recording-only',
        '--no-first-run',
        '--single-process',
        '--js-flags=--max-old-space-size=128',
      ],
    },
  });
}

async function transcribeAudio(base64Audio: string, mimetype: string): Promise<string> {
  // Use Gemini for audio transcription — it supports audio natively
  const { GoogleGenerativeAI } = await import('@google/generative-ai');
  const apiKey = config.gemini.apiKey;
  if (!apiKey) {
    throw new Error('GOOGLE_API_KEY not set — needed for voice transcription');
  }
  const genAI = new GoogleGenerativeAI(apiKey);
  const model = genAI.getGenerativeModel({ model: 'gemini-2.5-flash' });

  // Convert ogg/opus to a supported format if needed via inline data
  const result = await model.generateContent([
    {
      inlineData: {
        mimeType: mimetype,
        data: base64Audio,
      },
    },
    { text: 'Transcribe this audio exactly. Return ONLY the transcription text, nothing else. If the audio is unclear or empty, return "[inaudible]".' },
  ]);

  const text = result.response.text().trim();
  return text || '[inaudible]';
}

async function handleVoiceMessage(message: any): Promise<void> {
  const chat = await message.getChat();

  try {
    console.log('[WhatsApp] Voice message received, transcribing...');
    const media = await message.downloadMedia();
    if (!media || !media.data) {
      await sendBotMessage(chat, 'Could not download voice message.');
      return;
    }

    const transcript = await transcribeAudio(media.data, media.mimetype || 'audio/ogg');
    console.log(`[WhatsApp] Transcribed: ${transcript.substring(0, 100)}`);

    if (transcript === '[inaudible]' || !transcript) {
      await sendBotMessage(chat, 'Could not transcribe voice message (inaudible).');
      return;
    }

    // Show transcription to user
    await sendBotMessage(chat, `*Voice:* ${transcript}`);

    // Check if the transcription is a command
    const command = parseCommand(transcript);
    if (command) {
      await handleCommand(command, message);
      return;
    }

    // Otherwise, feed into the normal capture pipeline
    const senderId = message.from;
    const timestamp = new Date(message.timestamp * 1000).toISOString();

    await publish(config.subjects.inboxRaw, {
      rawText: transcript,
      source: 'whatsapp-voice',
      senderId,
      timestamp,
      messageId: message.id._serialized,
    });

    await logAudit({
      agentName: 'gateway',
      actionType: 'voice_transcribed',
      riskClass: 'R0',
      inputSummary: transcript.substring(0, 100),
      metadata: { senderId, messageId: message.id._serialized },
    });
  } catch (err) {
    console.error('[WhatsApp] Voice transcription error:', (err as Error).message);
    await sendBotMessage(chat, `Voice transcription failed: ${(err as Error).message}`);
  }
}

async function handleIncomingMessage(message: any): Promise<void> {
  // message_create fires for ALL messages (sent + received).
  // Only process messages we sent ourselves (fromMe) in self-chat.
  if (!message.fromMe) return;

  // Skip group messages and status broadcasts
  if (message.from.endsWith('@g.us') || message.from === 'status@broadcast') {
    return;
  }

  // FILTER: Only process messages from your own self-chat (you → you)
  if (myChatId && message.from !== myChatId) {
    return;
  }

  // Handle voice messages (ptt = push-to-talk, audio = audio files)
  const msgType = message.type;
  if ((msgType === 'ptt' || msgType === 'audio') && message.hasMedia) {
    await handleVoiceMessage(message);
    return;
  }

  // Handle document/file messages with no caption (non-command media)
  const rawText = message.body?.trim();
  if (!rawText) return;

  // Skip messages the bot itself sent (prevent feedback loop)
  if (recentBotMessages.has(rawText)) {
    recentBotMessages.delete(rawText);
    return;
  }

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
  const keywords = ['today', 'status', 'review', 'fix', 'search', 'run', 'kill', 'help', 'proj', 'task', 'gh', 'stale', 'plan', 'approve', 'deny', 'resume', 'agents', 'sb'];
  const firstWord = trimmed.split(/\s+/)[0].toLowerCase();
  if (keywords.includes(firstWord)) {
    const args = trimmed.split(/\s+/).slice(1);
    return { name: firstWord, args, raw: trimmed };
  }

  return null;
}

// Helper: send a message and track it so we don't re-process it
async function sendBotMessage(chat: any, text: string): Promise<void> {
  recentBotMessages.add(text);
  setTimeout(() => recentBotMessages.delete(text), 10000);
  await chat.sendMessage(text);
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
      await sendBotMessage(chat, summary);
      break;
    }

    case 'review': {
      const items = await db.query(
        `SELECT id, raw_text, classified_as, confidence FROM inbox_items
         WHERE status = 'review' ORDER BY created_at DESC LIMIT 5`,
      );
      if (items.rows.length === 0) {
        await sendBotMessage(chat, 'No items pending review.');
        return;
      }
      let msg = '*Review Queue*\n\n';
      items.rows.forEach((r: any, i: number) => {
        const shortId = r.id.substring(0, 8);
        msg += `${i + 1}. [${shortId}] "${r.raw_text.substring(0, 50)}..."\n`;
        msg += `   Guess: ${r.classified_as} (${(r.confidence * 100).toFixed(0)}%)\n`;
        msg += `   Fix: fix ${shortId} <category>\n\n`;
      });
      await sendBotMessage(chat, msg);
      break;
    }

    case 'fix': {
      const [shortId, newCategory] = cmd.args;
      if (!shortId || !newCategory) {
        await sendBotMessage(chat, 'Usage: fix <id> <category>\nCategories: idea, task, note, question, link');
        return;
      }
      const validCategories = ['idea', 'task', 'note', 'question', 'link'];
      if (!validCategories.includes(newCategory)) {
        await sendBotMessage(chat, `Invalid category. Use: ${validCategories.join(', ')}`);
        return;
      }
      const found = await db.query(
        `SELECT id, classified_as FROM inbox_items WHERE id::text LIKE $1 LIMIT 1`,
        [`${shortId}%`],
      );
      if (found.rows.length === 0) {
        await sendBotMessage(chat, `Item ${shortId} not found.`);
        return;
      }
      const { applyCorrection } = await import('../lib/store.js');
      await applyCorrection(found.rows[0].id, 'classified_as', found.rows[0].classified_as, newCategory);
      await logAudit({
        agentName: 'user',
        actionType: 'correction',
        inputSummary: `${shortId}: ${found.rows[0].classified_as} → ${newCategory}`,
      });
      await sendBotMessage(chat, `Fixed. ${shortId} → ${newCategory}`);
      break;
    }

    case 'search': {
      const searchText = cmd.args.join(' ');
      if (!searchText) {
        await sendBotMessage(chat, 'Usage: search <query>');
        return;
      }
      const results = await db.query(
        `SELECT id, title, node_type, priority FROM brain_nodes
         WHERE title ILIKE $1 OR content ILIKE $1
         ORDER BY created_at DESC LIMIT 5`,
        [`%${searchText}%`],
      );
      if (results.rows.length === 0) {
        await sendBotMessage(chat, `No results for "${searchText}"`);
        return;
      }
      let msg = `*Search: ${searchText}*\n\n`;
      results.rows.forEach((r: any, i: number) => {
        msg += `${i + 1}. [${r.node_type}] ${r.title} (${r.priority})\n`;
      });
      await sendBotMessage(chat, msg);
      break;
    }

    // ─── Project commands ──────────────────────────────────────────

    case 'proj': {
      const subCmd = cmd.args[0]?.toLowerCase();

      // proj (no args) — list all active projects
      if (!subCmd) {
        const projects = await listActiveProjects();
        if (projects.length === 0) {
          await sendBotMessage(chat, 'No active projects. Create one: proj add <name>');
          return;
        }
        let msg = '*Active Projects*\n\n';
        for (const p of projects) {
          msg += `*${p.name}*`;
          if (p.githubRepo) msg += ` (${p.githubRepo})`;
          msg += '\n';
        }
        await sendBotMessage(chat, msg);
        return;
      }

      // proj add <name> [repo]
      if (subCmd === 'add') {
        const name = cmd.args[1];
        const repo = cmd.args[2] || undefined;
        if (!name) {
          await sendBotMessage(chat, 'Usage: proj add <name> [owner/repo]');
          return;
        }
        try {
          await createProject(name, undefined, repo);
          let msg = `Project *${name}* created.`;
          if (repo) msg += ` Linked to ${repo}.`;
          await sendBotMessage(chat, msg);
        } catch (err) {
          const errMsg = (err as Error).message;
          if (errMsg.includes('unique') || errMsg.includes('duplicate')) {
            await sendBotMessage(chat, `Project "${name}" already exists.`);
          } else {
            await sendBotMessage(chat, `Failed to create project: ${errMsg}`);
          }
        }
        return;
      }

      // proj archive <name>
      if (subCmd === 'archive') {
        const name = cmd.args[1];
        if (!name) {
          await sendBotMessage(chat, 'Usage: proj archive <name>');
          return;
        }
        const ok = await archiveProject(name);
        await sendBotMessage(chat, ok ? `Project *${name}* archived.` : `Project "${name}" not found or already archived.`);
        return;
      }

      // proj repo <name> <owner/repo>
      if (subCmd === 'repo') {
        const name = cmd.args[1];
        const repo = cmd.args[2];
        if (!name || !repo) {
          await sendBotMessage(chat, 'Usage: proj repo <name> <owner/repo>');
          return;
        }
        const ok = await updateProjectRepo(name, repo);
        await sendBotMessage(chat, ok ? `*${name}* linked to ${repo}.` : `Project "${name}" not found.`);
        return;
      }

      // proj path <name> <path>
      if (subCmd === 'path') {
        const name = cmd.args[1];
        const path = cmd.args.slice(2).join(' ');
        if (!name || !path) {
          await sendBotMessage(chat, 'Usage: proj path <name> <local-path>');
          return;
        }
        const ok = await updateProjectPath(name, path);
        await sendBotMessage(chat, ok ? `*${name}* path set to ${path}` : `Project "${name}" not found.`);
        return;
      }

      // proj <name> — project dashboard
      {
        const name = cmd.args.join(' ');
        const project = await getProjectByName(name);
        if (!project) {
          await sendBotMessage(chat, `Project "${name}" not found. Send "proj" to list projects.`);
          return;
        }
        const dashboard = await getProjectDashboard(project.id);
        if (!dashboard) {
          await sendBotMessage(chat, `Could not load dashboard for "${name}".`);
          return;
        }

        let msg = `*Project: ${dashboard.project.name}*\n`;
        if (dashboard.project.githubRepo) msg += `GitHub: ${dashboard.project.githubRepo}\n`;
        if (dashboard.project.localPath) msg += `Path: ${dashboard.project.localPath}\n`;
        msg += `\nTasks: ${dashboard.taskCounts.active} active | ${dashboard.taskCounts.completed} done | ${dashboard.taskCounts.archived} archived\n`;

        if (dashboard.recentNodes.length > 0) {
          msg += '\n*Recent brain nodes:*\n';
          for (const n of dashboard.recentNodes) {
            msg += `  [${n.nodeType}] ${n.title}\n`;
          }
        }

        if (dashboard.recentItems.length > 0) {
          msg += '\n*Recent inbox items:*\n';
          for (const i of dashboard.recentItems) {
            msg += `  [${i.classifiedAs}] ${i.rawText.substring(0, 50)}\n`;
          }
        }

        // Append GitHub status if available
        if (dashboard.project.githubRepo) {
          const repoStatus = await getLatestRepoStatus(dashboard.project.id);
          if (repoStatus) {
            msg += `\nGitHub: PRs ${repoStatus.openPrs} | Issues ${repoStatus.openIssues}`;
            if (repoStatus.failingChecks > 0) msg += ` | Failing ${repoStatus.failingChecks}`;
            msg += '\n';
          }
        }

        await sendBotMessage(chat, msg);
      }
      break;
    }

    // ─── Task commands ─────────────────────────────────────────────

    case 'task': {
      const subCmd = cmd.args[0]?.toLowerCase();

      // task add <project> <title...>
      if (subCmd === 'add') {
        const projectName = cmd.args[1];
        const title = cmd.args.slice(2).join(' ');
        if (!projectName || !title) {
          await sendBotMessage(chat, 'Usage: task add <project> <title>');
          return;
        }
        const project = await getProjectByName(projectName);
        if (!project) {
          await sendBotMessage(chat, `Project "${projectName}" not found.`);
          return;
        }
        const taskId = await createTask(project.id, title);
        await sendBotMessage(chat, `Task created in *${project.name}*: ${title}\nID: ${taskId.substring(0, 8)}`);
        return;
      }

      // task done <id>
      if (subCmd === 'done') {
        const shortId = cmd.args[1];
        if (!shortId) {
          await sendBotMessage(chat, 'Usage: task done <id>');
          return;
        }
        const found = await db.query(
          `SELECT id, title FROM brain_nodes WHERE id::text LIKE $1 AND node_type = 'task' LIMIT 1`,
          [`${shortId}%`],
        );
        if (found.rows.length === 0) {
          await sendBotMessage(chat, `Task ${shortId} not found.`);
          return;
        }
        await completeTask(found.rows[0].id);
        await sendBotMessage(chat, `Done: ${found.rows[0].title}`);
        return;
      }

      // task drop <id>
      if (subCmd === 'drop') {
        const shortId = cmd.args[1];
        if (!shortId) {
          await sendBotMessage(chat, 'Usage: task drop <id>');
          return;
        }
        const found = await db.query(
          `SELECT id, title FROM brain_nodes WHERE id::text LIKE $1 AND node_type = 'task' LIMIT 1`,
          [`${shortId}%`],
        );
        if (found.rows.length === 0) {
          await sendBotMessage(chat, `Task ${shortId} not found.`);
          return;
        }
        await archiveTask(found.rows[0].id);
        await sendBotMessage(chat, `Dropped: ${found.rows[0].title}`);
        return;
      }

      // task <project> — list tasks for project
      {
        const projectName = cmd.args.join(' ');
        if (!projectName) {
          await sendBotMessage(chat, 'Usage: task <project> | task add <project> <title> | task done <id> | task drop <id>');
          return;
        }
        const project = await getProjectByName(projectName);
        if (!project) {
          await sendBotMessage(chat, `Project "${projectName}" not found.`);
          return;
        }
        const tasks = await listProjectTasks(project.id);
        if (tasks.length === 0) {
          await sendBotMessage(chat, `No active tasks for *${project.name}*.\nCreate one: task add ${project.name} <title>`);
          return;
        }
        let msg = `*Tasks: ${project.name}*\n\n`;
        for (const t of tasks) {
          const shortId = t.id.substring(0, 8);
          const pri = t.priority !== 'medium' ? ` [${t.priority}]` : '';
          msg += `${shortId} ${t.title}${pri}\n`;
        }
        msg += `\nDone: task done <id> | Drop: task drop <id>`;
        await sendBotMessage(chat, msg);
      }
      break;
    }

    // ─── GitHub commands ───────────────────────────────────────────

    case 'gh': {
      const subCmd = cmd.args[0]?.toLowerCase();

      // gh sync — force sync
      if (subCmd === 'sync') {
        if (!config.github.token) {
          await sendBotMessage(chat, 'GITHUB_TOKEN not set. Cannot sync.');
          return;
        }
        await sendBotMessage(chat, 'Syncing GitHub repos...');
        try {
          const count = await syncAllRepos();
          await sendBotMessage(chat, `Synced ${count} repo(s).`);
        } catch (err) {
          await sendBotMessage(chat, `Sync failed: ${(err as Error).message}`);
        }
        return;
      }

      // gh <project> — single project GitHub status
      if (subCmd) {
        const projectName = cmd.args.join(' ');
        const project = await getProjectByName(projectName);
        if (!project) {
          await sendBotMessage(chat, `Project "${projectName}" not found.`);
          return;
        }
        if (!project.githubRepo) {
          await sendBotMessage(chat, `*${project.name}* has no GitHub repo linked.\nLink one: proj repo ${project.name} owner/repo`);
          return;
        }
        const status = await getLatestRepoStatus(project.id);
        if (!status) {
          await sendBotMessage(chat, `No GitHub data yet for *${project.name}*. Run "gh sync" first.`);
          return;
        }
        await sendBotMessage(chat, formatRepoStatus(project.name, status));
        return;
      }

      // gh (no args) — summary across all repos
      {
        const projects = await listActiveProjects();
        const reposWithStatus: Array<{ projectName: string; snapshot: RepoSnapshot }> = [];
        for (const p of projects) {
          if (!p.githubRepo) continue;
          const status = await getLatestRepoStatus(p.id);
          if (status) {
            reposWithStatus.push({ projectName: p.name, snapshot: status });
          }
        }
        await sendBotMessage(chat, formatAllReposSummary(reposWithStatus));
      }
      break;
    }

    // ─── Stale command ─────────────────────────────────────────────

    case 'stale': {
      const items = await getStaleItems(config.nudge.staleDaysThreshold);
      if (items.length === 0) {
        await sendBotMessage(chat, `Nothing stale (threshold: ${config.nudge.staleDaysThreshold} days).`);
        return;
      }
      let msg = `*Stale Items* (>${config.nudge.staleDaysThreshold} days)\n\n`;
      for (const item of items) {
        const daysAgo = Math.floor((Date.now() - item.updatedAt.getTime()) / (1000 * 60 * 60 * 24));
        msg += `[${item.nodeType}] ${item.title} (${daysAgo}d ago)`;
        if (item.projectName) msg += ` — ${item.projectName}`;
        msg += '\n';
      }
      await sendBotMessage(chat, msg);
      break;
    }

    case 'help': {
      await sendBotMessage(chat,
        '*duSraBheja Commands*\n\n' +
        '*Capture*\n' +
        '? — Today\'s brain summary\n' +
        '+ <text> — Quick add\n' +
        '! <text> — Urgent capture\n' +
        'today — Daily status\n' +
        'review — Show review queue\n' +
        'fix <id> <category> — Correct classification\n' +
        'search <query> — Search brain\n' +
        '\n*Projects*\n' +
        'proj — List all projects\n' +
        'proj <name> — Project dashboard\n' +
        'proj add <name> [repo] — Create project\n' +
        'proj archive <name> — Archive project\n' +
        'proj repo <name> <owner/repo> — Link GitHub\n' +
        'proj path <name> <path> — Set local path\n' +
        '\n*Tasks*\n' +
        'task <project> — List tasks\n' +
        'task add <project> <title> — Create task\n' +
        'task done <id> — Complete task\n' +
        'task drop <id> — Drop task\n' +
        '\n*GitHub*\n' +
        'gh — All repos summary\n' +
        'gh <project> — Project GitHub status\n' +
        'gh sync — Force sync now\n' +
        '\n*Agents*\n' +
        'plan <desc> — Generate plan with Planner + Critic\n' +
        'run <desc> — Plan + auto-execute (if R0-R2)\n' +
        'approve — Approve pending agent action\n' +
        'deny — Deny pending agent action\n' +
        'kill — Emergency stop + lockdown\n' +
        'resume — Exit lockdown\n' +
        'agents — Show running agents + status\n' +
        '\n*Storyboard*\n' +
        'sb <text> — Manga storyboard from any text\n' +
        'sb tasks <project> — Project task board\n' +
        'sb ideas — Recent brain ideas board\n' +
        'sb last — Last agent workflow storyboard\n' +
        '\n*System*\n' +
        'stale — Show stale items\n' +
        'status — System status\n' +
        'help — This message',
      );
      break;
    }

    case 'urgent': {
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
      serviceStatus.push('WhatsApp: up');
      serviceStatus.push(`GitHub poller: ${config.github.token ? 'active' : 'disabled (no token)'}`);
      serviceStatus.push(`Nudge checker: active (${config.nudge.checkIntervalHours}h interval)`);
      await sendBotMessage(chat, `*System Status*\n${serviceStatus.join('\n')}`);
      break;
    }

    // ─── Agent commands ──────────────────────────────────────────

    case 'plan': {
      const desc = cmd.args.join(' ');
      if (!desc) {
        await sendBotMessage(chat, 'Usage: plan <description>');
        return;
      }
      if (isLockdown()) {
        await sendBotMessage(chat, 'LOCKDOWN ACTIVE. Send *resume* first.');
        return;
      }
      await sendBotMessage(chat, `Planning: ${desc}...`);
      const chatId = message.from;
      runAgentChain(desc, { chatId, traceId: uuidv4(), triggeredBy: 'whatsapp' }, false)
        .catch((err) => sendBotMessage(chat, `Plan failed: ${(err as Error).message}`));
      break;
    }

    case 'run': {
      const desc = cmd.args.join(' ');
      if (!desc) {
        await sendBotMessage(chat, 'Usage: run <description>');
        return;
      }
      if (isLockdown()) {
        await sendBotMessage(chat, 'LOCKDOWN ACTIVE. Send *resume* first.');
        return;
      }
      await sendBotMessage(chat, `Running: ${desc}...`);
      const chatId = message.from;
      runAgentChain(desc, { chatId, traceId: uuidv4(), triggeredBy: 'whatsapp' }, true)
        .catch((err) => sendBotMessage(chat, `Run failed: ${(err as Error).message}`));
      break;
    }

    case 'approve': {
      const pending = await getPendingApproval();
      if (!pending) {
        await sendBotMessage(chat, 'No pending approval requests.');
        return;
      }
      await resolveApproval(pending.id, 'approved');
      await sendBotMessage(chat, `Approved: ${pending.summary}`);
      break;
    }

    case 'deny': {
      const pending = await getPendingApproval();
      if (!pending) {
        await sendBotMessage(chat, 'No pending approval requests.');
        return;
      }
      await resolveApproval(pending.id, 'denied');
      await sendBotMessage(chat, `Denied: ${pending.summary}`);
      break;
    }

    case 'kill': {
      const traceId = uuidv4();
      const count = await activateKillSwitch(traceId);
      await sendBotMessage(chat, formatKillConfirmation(count));
      break;
    }

    case 'resume': {
      if (!isLockdown()) {
        await sendBotMessage(chat, 'Not in lockdown mode.');
        return;
      }
      const traceId = uuidv4();
      await resumeFromLockdown(traceId);
      await sendBotMessage(chat, formatResumeConfirmation());
      break;
    }

    case 'agents': {
      const activeRuns = await getActiveRuns();
      const recentRuns = await getRecentRuns(5);
      await sendBotMessage(chat, formatAgentStatus(activeRuns, recentRuns, isLockdown()));
      break;
    }

    // ─── Storyboard commands ────────────────────────────────────

    case 'sb': {
      const subCmd = cmd.args[0]?.toLowerCase();

      if (subCmd === 'tasks') {
        const projectName = cmd.args.slice(1).join(' ');
        if (!projectName) {
          await sendBotMessage(chat, 'Usage: sb tasks <project>');
          return;
        }
        const project = await getProjectByName(projectName);
        if (!project) {
          await sendBotMessage(chat, `Project "${projectName}" not found.`);
          return;
        }
        await sendBotMessage(chat, 'Generating task storyboard...');
        storyboardFromTasks(project.id, message.from)
          .catch((err: Error) => sendBotMessage(chat, `Storyboard failed: ${err.message}`));
        return;
      }

      if (subCmd === 'ideas') {
        await sendBotMessage(chat, 'Generating ideas storyboard...');
        storyboardFromIdeas(message.from)
          .catch((err: Error) => sendBotMessage(chat, `Storyboard failed: ${err.message}`));
        return;
      }

      if (subCmd === 'last') {
        await sendBotMessage(chat, 'Last agent storyboard is saved in storyboards/ folder.\n(Auto-generated with each agent run)');
        return;
      }

      // Check for PDF attachment
      if (message.hasMedia) {
        await sendBotMessage(chat, 'Extracting PDF text...');
        (async () => {
          try {
            const media = await message.downloadMedia();
            if (!media || !media.mimetype?.includes('pdf')) {
              await sendBotMessage(chat, 'Only PDF files are supported for storyboarding. Send a PDF with caption "sb".');
              return;
            }
            const pdfParse = (await import('pdf-parse')).default;
            const buffer = Buffer.from(media.data, 'base64');
            const pdfData = await pdfParse(buffer);
            const pdfText = pdfData.text?.trim();
            if (!pdfText) {
              await sendBotMessage(chat, 'Could not extract text from PDF (empty or image-only).');
              return;
            }
            // Truncate to ~3000 chars to keep Ollama prompt reasonable
            const truncated = pdfText.substring(0, 3000);
            const label = media.filename || 'PDF document';
            await sendBotMessage(chat, `Extracted ${pdfText.length} chars from ${label}. Generating storyboard...`);
            await storyboardFromText(truncated, message.from);
          } catch (err) {
            await sendBotMessage(chat, `PDF storyboard failed: ${(err as Error).message}`);
          }
        })();
        break;
      }

      // sb <text> — general text storyboard
      const text = cmd.args.join(' ');
      if (!text) {
        await sendBotMessage(chat, 'Usage: sb <text> | sb tasks <project> | sb ideas | sb last\nOr send a PDF with caption "sb"');
        return;
      }
      await sendBotMessage(chat, 'Generating storyboard...');
      storyboardFromText(text, message.from)
        .catch((err: Error) => sendBotMessage(chat, `Storyboard failed: ${err.message}`));
      break;
    }

    default:
      await sendBotMessage(chat, `Unknown command: ${cmd.name}. Send "help" for available commands.`);
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
      const { chatId, text, imagePath, caption } = data;

      if (!whatsappClient || !chatId) continue;

      // Handle image messages (storyboards)
      if (imagePath) {
        try {
          const { MessageMedia } = pkg;
          const media = MessageMedia.fromFilePath(imagePath);
          await whatsappClient.sendMessage(chatId, media, { caption: caption || '' });
          console.log(`[WhatsApp] Sent image to ${chatId}: ${imagePath}`);
        } catch (imgErr) {
          console.error('[WhatsApp] Image send failed:', (imgErr as Error).message);
          // Fallback to text
          if (caption) {
            await whatsappClient.sendMessage(chatId, `[Image unavailable] ${caption}`);
          }
        }
        continue;
      }

      if (text) {
        recentBotMessages.add(text);
        setTimeout(() => recentBotMessages.delete(text), 10000);
        await whatsappClient.sendMessage(chatId, text);
        console.log(`[WhatsApp] Sent reply to ${chatId}: ${text.substring(0, 60)}...`);
      }
    } catch (err) {
      console.error('[WhatsApp] Outbound error:', (err as Error).message);
    }
  }
}

export async function startGateway(): Promise<void> {
  console.log('[WhatsApp] Starting gateway...');

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

  client.on('ready', async () => {
    const info = client.info;
    myChatId = info?.wid?._serialized || null;
    console.log(`[WhatsApp] Client ready and connected`);
    console.log(`[WhatsApp] Bot active ONLY in self-chat: ${myChatId}`);
    console.log(`[WhatsApp] All other chats are ignored.`);
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

  client.on('message_create', handleIncomingMessage);

  await client.initialize();
}
