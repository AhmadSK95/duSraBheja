import { spawn, ChildProcess } from 'child_process';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const processes: ChildProcess[] = [];

function startWorker(name: string, script: string): ChildProcess {
  console.log(`[Main] Starting ${name}...`);
  const proc = spawn('npx', ['tsx', script], {
    cwd: path.resolve(__dirname, '..'),
    stdio: 'inherit',
    env: { ...process.env },
  });

  proc.on('exit', (code) => {
    console.log(`[Main] ${name} exited with code ${code}`);
  });

  processes.push(proc);
  return proc;
}

async function main(): Promise<void> {
  console.log('');
  console.log('═══════════════════════════════════════════');
  console.log('  duSraBheja — Solo AI Command Center');
  console.log('  Starting Phase 1 Core Loop...');
  console.log('═══════════════════════════════════════════');
  console.log('');

  // Start workers in order
  startWorker('WhatsApp Gateway', 'src/gateway/whatsapp.ts');

  // Small delay to let WhatsApp connect first
  await new Promise((r) => setTimeout(r, 2000));

  startWorker('Inbox Processor', 'src/workers/inbox-processor.ts');
  startWorker('Responder', 'src/workers/responder.ts');

  console.log('[Main] All workers started. Ctrl+C to stop.');
}

const shutdown = () => {
  console.log('\n[Main] Shutting down all workers...');
  processes.forEach((p) => p.kill('SIGTERM'));
  setTimeout(() => process.exit(0), 3000);
};

process.on('SIGINT', shutdown);
process.on('SIGTERM', shutdown);

main().catch((err) => {
  console.error('[Main] Fatal error:', err);
  process.exit(1);
});
