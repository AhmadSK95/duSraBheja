import { Ollama } from 'ollama';
import { config } from '../lib/config.js';
import { publish } from '../lib/nats-client.js';
import { query } from '../lib/db.js';
import { v4 as uuidv4 } from 'uuid';
import { writeFile, mkdir } from 'fs/promises';
import { join } from 'path';
import type { AgentGraphState } from './types.js';

const ollama = new Ollama({ host: config.ollama.host });

// ─── Panel Description Types ────────────────────────────────────────────────

interface Panel {
  title: string;
  content: string;
  character?: string; // avatar type
  mood?: 'neutral' | 'excited' | 'thinking' | 'warning' | 'success' | 'error';
  stamp?: string; // APPROVED, DENIED, etc.
}

// ─── Character Avatars (SVG-based) ──────────────────────────────────────────

const AVATARS: Record<string, string> = {
  planner: `<svg viewBox="0 0 40 40" width="40" height="40"><circle cx="20" cy="20" r="18" fill="#4A90D9"/><text x="20" y="26" text-anchor="middle" fill="white" font-size="18">🧠</text></svg>`,
  critic: `<svg viewBox="0 0 40 40" width="40" height="40"><circle cx="20" cy="20" r="18" fill="#E8573A"/><text x="20" y="26" text-anchor="middle" fill="white" font-size="18">🔍</text></svg>`,
  executor: `<svg viewBox="0 0 40 40" width="40" height="40"><circle cx="20" cy="20" r="18" fill="#2ECC71"/><text x="20" y="26" text-anchor="middle" fill="white" font-size="18">⚡</text></svg>`,
  sentinel: `<svg viewBox="0 0 40 40" width="40" height="40"><circle cx="20" cy="20" r="18" fill="#F39C12"/><text x="20" y="26" text-anchor="middle" fill="white" font-size="18">🛡️</text></svg>`,
  narrator: `<svg viewBox="0 0 40 40" width="40" height="40"><circle cx="20" cy="20" r="18" fill="#9B59B6"/><text x="20" y="26" text-anchor="middle" fill="white" font-size="18">📖</text></svg>`,
  idea: `<svg viewBox="0 0 40 40" width="40" height="40"><circle cx="20" cy="20" r="18" fill="#F1C40F"/><text x="20" y="26" text-anchor="middle" fill="white" font-size="18">💡</text></svg>`,
  task: `<svg viewBox="0 0 40 40" width="40" height="40"><circle cx="20" cy="20" r="18" fill="#3498DB"/><text x="20" y="26" text-anchor="middle" fill="white" font-size="18">✅</text></svg>`,
  default: `<svg viewBox="0 0 40 40" width="40" height="40"><circle cx="20" cy="20" r="18" fill="#95A5A6"/><text x="20" y="26" text-anchor="middle" fill="white" font-size="18">⭐</text></svg>`,
};

// ─── HTML Template Engine ───────────────────────────────────────────────────

function buildMangaHTML(title: string, panels: Panel[], footer?: string): string {
  const moodColors: Record<string, string> = {
    neutral: '#2C3E50',
    excited: '#27AE60',
    thinking: '#2980B9',
    warning: '#F39C12',
    success: '#2ECC71',
    error: '#E74C3C',
  };

  const panelHTML = panels.map((panel, i) => {
    const mood = panel.mood || 'neutral';
    const borderColor = moodColors[mood] || moodColors.neutral;
    const avatar = AVATARS[panel.character || 'default'] || AVATARS.default;
    const tilt = i % 2 === 0 ? 'rotate(-0.5deg)' : 'rotate(0.5deg)';
    const stamp = panel.stamp
      ? `<div class="stamp stamp-${panel.stamp.toLowerCase()}">${panel.stamp}</div>`
      : '';

    return `
      <div class="panel" style="border-color: ${borderColor}; transform: ${tilt};">
        ${stamp}
        <div class="panel-header">
          <div class="avatar">${avatar}</div>
          <div class="panel-title">${escapeHtml(panel.title)}</div>
        </div>
        <div class="speech-bubble" style="border-left-color: ${borderColor};">
          ${escapeHtml(panel.content)}
        </div>
      </div>`;
  }).join('\n');

  const footerHTML = footer ? `<div class="footer">${escapeHtml(footer)}</div>` : '';

  return `<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    background: #1a1a2e;
    font-family: 'Segoe UI', system-ui, sans-serif;
    padding: 20px;
    width: 800px;
  }
  .header {
    text-align: center;
    padding: 15px;
    margin-bottom: 20px;
    background: linear-gradient(135deg, #16213e, #0f3460);
    border: 3px solid #e94560;
    border-radius: 10px;
  }
  .header h1 {
    color: #e94560;
    font-size: 22px;
    font-weight: 900;
    text-transform: uppercase;
    letter-spacing: 2px;
  }
  .header .subtitle {
    color: #a8a8a8;
    font-size: 13px;
    margin-top: 4px;
  }
  .panels {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 15px;
  }
  .panel {
    background: #16213e;
    border: 3px solid #333;
    border-radius: 8px;
    padding: 15px;
    position: relative;
    transition: transform 0.1s;
  }
  .panel:only-child,
  .panels > .panel:last-child:nth-child(odd) {
    grid-column: 1 / -1;
  }
  .panel-header {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 10px;
  }
  .avatar { flex-shrink: 0; }
  .panel-title {
    color: #eee;
    font-size: 15px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 1px;
  }
  .speech-bubble {
    background: #0f3460;
    border-left: 4px solid #333;
    border-radius: 0 8px 8px 0;
    padding: 10px 12px;
    color: #ccc;
    font-size: 13px;
    line-height: 1.5;
    white-space: pre-wrap;
  }
  .stamp {
    position: absolute;
    top: 10px;
    right: 10px;
    font-size: 14px;
    font-weight: 900;
    padding: 4px 10px;
    border: 3px solid;
    border-radius: 4px;
    transform: rotate(12deg);
    opacity: 0.9;
    text-transform: uppercase;
    letter-spacing: 1px;
  }
  .stamp-approved { color: #2ECC71; border-color: #2ECC71; }
  .stamp-denied { color: #E74C3C; border-color: #E74C3C; }
  .stamp-timeout { color: #F39C12; border-color: #F39C12; }
  .stamp-running { color: #3498DB; border-color: #3498DB; }
  .stamp-done { color: #2ECC71; border-color: #2ECC71; }
  .footer {
    text-align: center;
    color: #666;
    font-size: 12px;
    margin-top: 15px;
    padding-top: 10px;
    border-top: 1px solid #333;
  }
</style>
</head>
<body>
  <div class="header">
    <h1>duSraBheja</h1>
    <div class="subtitle">${escapeHtml(title)}</div>
  </div>
  <div class="panels">
    ${panelHTML}
  </div>
  ${footerHTML}
</body>
</html>`;
}

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// ─── Puppeteer Rendering ────────────────────────────────────────────────────

async function renderAndSend(html: string, chatId: string, storyboardId?: string): Promise<void> {
  const id = storyboardId || uuidv4();

  // Save HTML for browser viewing
  const dir = join(process.cwd(), 'storyboards');
  await mkdir(dir, { recursive: true });
  const htmlPath = join(dir, `${id}.html`);
  await writeFile(htmlPath, html, 'utf-8');

  // Try puppeteer rendering — use dynamic import to reuse whatsapp-web.js's puppeteer
  try {
    const puppeteer = await import('puppeteer');
    const browser = await puppeteer.default.launch({
      headless: true,
      args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-gpu'],
    });
    const page = await browser.newPage();
    await page.setViewport({ width: 800, height: 1200 });
    await page.setContent(html, { waitUntil: 'networkidle0' });

    // Auto-size to content height
    const bodyHeight = await page.evaluate(() => (globalThis as any).document.body.scrollHeight);
    await page.setViewport({ width: 800, height: Math.min(bodyHeight + 40, 2400) });

    const pngPath = join(dir, `${id}.png`);
    await page.screenshot({ path: pngPath, fullPage: true });
    await browser.close();

    // Send image via WhatsApp — publish path for whatsapp gateway to send
    await publish(config.subjects.whatsappOutbound, {
      chatId,
      imagePath: pngPath,
      caption: `Storyboard: ${id.substring(0, 8)}`,
    });
  } catch (err) {
    // Fallback: send HTML file path as text message
    console.warn('[Storyboard] Puppeteer render failed, sending text fallback:', (err as Error).message);
    await publish(config.subjects.whatsappOutbound, {
      chatId,
      text: `Storyboard saved: storyboards/${id}.html\n(Puppeteer render unavailable)`,
    });
  }
}

// ─── Public Storyboard Functions ────────────────────────────────────────────

export async function storyboardFromText(text: string, chatId: string): Promise<void> {
  // Use Ollama to break text into panel descriptions
  const response = await ollama.generate({
    model: config.ollama.classifyModel,
    prompt: `Break the following concept into 3-6 visual manga panels. For each panel, provide a title (5 words max) and content (2-3 sentences explaining that part of the concept).

Respond in valid JSON only:
{ "panels": [{ "title": "string", "content": "string" }] }

Concept: ${text}`,
    stream: false,
    options: { temperature: 0.5, num_predict: 800 },
  });

  const jsonMatch = response.response.match(/\{[\s\S]*\}/);
  let panels: Panel[];

  if (jsonMatch) {
    try {
      const parsed = JSON.parse(jsonMatch[0]);
      panels = (parsed.panels || []).map((p: any, i: number) => ({
        title: p.title || `Panel ${i + 1}`,
        content: p.content || '',
        character: 'narrator',
        mood: 'thinking' as const,
      }));
    } catch {
      panels = [{ title: 'Concept', content: text, character: 'narrator', mood: 'neutral' }];
    }
  } else {
    panels = [{ title: 'Concept', content: text, character: 'narrator', mood: 'neutral' }];
  }

  const html = buildMangaHTML(`Storyboard: ${text.substring(0, 50)}`, panels);
  await renderAndSend(html, chatId);
}

export async function storyboardFromTasks(projectId: string, chatId: string): Promise<void> {
  const result = await query<any>(
    `SELECT bn.title, bn.priority, bn.status, p.name as project_name
     FROM brain_nodes bn
     LEFT JOIN projects p ON bn.project_id = p.id
     WHERE bn.project_id = $1 AND bn.node_type = 'task'
     ORDER BY
       CASE bn.priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,
       bn.created_at DESC
     LIMIT 12`,
    [projectId],
  );

  const projectName = result.rows[0]?.project_name || 'Project';

  const panels: Panel[] = result.rows.map((r: any) => {
    const isDone = r.status === 'completed' || r.status === 'archived';
    return {
      title: r.title.substring(0, 30),
      content: `Priority: ${r.priority}\nStatus: ${r.status}`,
      character: 'task',
      mood: isDone ? 'success' : r.priority === 'urgent' ? 'error' : r.priority === 'high' ? 'warning' : 'neutral',
      stamp: isDone ? 'DONE' : undefined,
    } as Panel;
  });

  if (panels.length === 0) {
    panels.push({ title: 'No Tasks', content: 'No tasks found for this project.', character: 'default', mood: 'neutral' });
  }

  const html = buildMangaHTML(`Task Board: ${projectName}`, panels);
  await renderAndSend(html, chatId);
}

export async function storyboardFromIdeas(chatId: string): Promise<void> {
  const result = await query<any>(
    `SELECT title, content, priority, created_at
     FROM brain_nodes
     WHERE node_type = 'idea' AND status = 'active'
     ORDER BY created_at DESC
     LIMIT 8`,
  );

  const panels: Panel[] = result.rows.map((r: any) => ({
    title: r.title.substring(0, 30),
    content: (r.content || r.title).substring(0, 120),
    character: 'idea',
    mood: r.priority === 'high' || r.priority === 'urgent' ? 'excited' : 'thinking',
  }));

  if (panels.length === 0) {
    panels.push({ title: 'No Ideas Yet', content: 'Capture some ideas first!', character: 'idea', mood: 'neutral' });
  }

  const html = buildMangaHTML('Idea Board', panels, `${panels.length} recent ideas`);
  await renderAndSend(html, chatId);
}

export async function storyboardFromAgentRun(state: AgentGraphState, chatId: string): Promise<void> {
  const panels: Panel[] = [];

  // Panel 1: Task
  panels.push({
    title: 'Mission',
    content: state.taskDescription.substring(0, 150),
    character: 'narrator',
    mood: 'neutral',
  });

  // Panel 2: Planner
  if (state.plan) {
    const recOpt = state.plan.options[state.plan.recommendedIndex];
    panels.push({
      title: 'Planner',
      content: `${state.plan.options.length} options.\nRecommended: ${recOpt?.label || '?'}\n${recOpt?.steps.slice(0, 3).join(' → ') || ''}`,
      character: 'planner',
      mood: 'thinking',
    });
  }

  // Panel 3: Critic
  if (state.review) {
    panels.push({
      title: 'Critic',
      content: `Score: ${state.review.score}/10\n${state.review.approved ? 'Approved' : 'Rejected'}\n${state.review.issues.slice(0, 2).join('; ') || 'No issues'}`,
      character: 'critic',
      mood: state.review.approved ? 'success' : 'warning',
      stamp: state.review.approved ? 'APPROVED' : 'DENIED',
    });
  }

  // Panel 4: Sentinel
  if (state.sentinelDecision) {
    panels.push({
      title: 'Sentinel',
      content: `Decision: ${state.sentinelDecision}\nRisk: ${state.plan?.options[state.selectedOptionIndex]?.riskClass || '?'}\n${state.sentinelReason || ''}`,
      character: 'sentinel',
      mood: state.sentinelDecision === 'allow' ? 'success' : state.sentinelDecision === 'deny' ? 'error' : 'warning',
    });
  }

  // Panel 5: Executor
  if (state.executionResult) {
    panels.push({
      title: 'Executor',
      content: `${state.executionResult.result.substring(0, 120)}\n${state.executionResult.actions.length} actions completed`,
      character: 'executor',
      mood: 'success',
      stamp: 'DONE',
    });
  } else if (state.approvalStatus === 'denied' || state.approvalStatus === 'timeout') {
    panels.push({
      title: 'Halted',
      content: `Status: ${state.approvalStatus}\nExecution was not performed.`,
      character: 'executor',
      mood: 'error',
      stamp: state.approvalStatus.toUpperCase(),
    });
  }

  const footer = `Tokens: ${state.totalTokens} | Cost: $${state.totalCost.toFixed(3)} | Time: ${(state.totalDurationMs / 1000).toFixed(1)}s`;
  const html = buildMangaHTML('Agent Workflow', panels, footer);
  await renderAndSend(html, chatId, state.context.traceId);
}
