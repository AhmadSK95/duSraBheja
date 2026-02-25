import { startGateway } from './gateway/whatsapp.js';
import { startInboxProcessor } from './workers/inbox-processor.js';
import { startResponder } from './workers/responder.js';
import { startGitHubPoller } from './workers/github-poller.js';
import { startNudgeChecker } from './workers/nudge-checker.js';
import { startAgentSubsystem, stopAgentSubsystem } from './agents/index.js';
import { ensureStream } from './lib/nats-client.js';
import { shutdown as natsShutdown } from './lib/nats-client.js';
import { shutdown as dbShutdown } from './lib/db.js';
import { config } from './lib/config.js';

async function main(): Promise<void> {
  console.log('');
  console.log('═══════════════════════════════════════════');
  console.log('  duSraBheja — Solo AI Command Center');
  console.log('═══════════════════════════════════════════');
  console.log('');

  // Ensure NATS streams exist for all subsystems
  await ensureStream('GITHUB', [config.subjects.githubSync, config.subjects.githubAlert]);
  await ensureStream('NUDGE', [config.subjects.nudgeSend]);
  await ensureStream('AGENT', [config.subjects.agentRun, config.subjects.agentComplete], 50);
  await ensureStream('SYSTEM', [config.subjects.systemKill, config.subjects.systemResume], 10);

  // Start responder first (lightweight, just NATS subscriptions)
  await startResponder();

  // Start inbox processor (NATS consumer loop)
  startInboxProcessor().catch((err) => {
    console.error('[Main] Inbox Processor error:', err);
  });

  // Start GitHub poller (conditional on GITHUB_TOKEN)
  await startGitHubPoller();

  // Start nudge checker (stale item alerts)
  await startNudgeChecker();

  // Start agent subsystem (policy engine + approval poller)
  await startAgentSubsystem();

  // Start WhatsApp gateway (heaviest — launches Chromium)
  await startGateway();

  console.log('[Main] All services running in single process.');
}

const shutdown = async () => {
  console.log('\n[Main] Shutting down...');
  stopAgentSubsystem();
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
