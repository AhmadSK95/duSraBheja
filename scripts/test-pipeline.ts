/**
 * Phase 1 Integration Test
 * Tests: NATS publish → Inbox Processor → Ollama classify → Postgres store → Audit
 * Run: npx tsx scripts/test-pipeline.ts
 */

import { publish, ensureStream, getNatsConnection, shutdown as natsShutdown } from '../src/lib/nats-client.js';
import { query, shutdown as dbShutdown } from '../src/lib/db.js';
import { classify, generateEmbedding } from '../src/lib/ollama-client.js';
import { createInboxItem, createBrainNode, getTodayItems } from '../src/lib/store.js';
import { logAudit } from '../src/lib/audit.js';
import { config } from '../src/lib/config.js';

const GREEN = '\x1b[32m';
const RED = '\x1b[31m';
const YELLOW = '\x1b[33m';
const NC = '\x1b[0m';

let passed = 0;
let failed = 0;

async function test(name: string, fn: () => Promise<void>): Promise<void> {
  try {
    await fn();
    console.log(`  ${GREEN}✓${NC} ${name}`);
    passed++;
  } catch (err) {
    console.log(`  ${RED}✗${NC} ${name}: ${(err as Error).message}`);
    failed++;
  }
}

function assert(condition: boolean, msg: string): void {
  if (!condition) throw new Error(msg);
}

async function main(): Promise<void> {
  console.log('\n═══════════════════════════════════════════');
  console.log('  duSraBheja Phase 1 Pipeline Test');
  console.log('═══════════════════════════════════════════\n');

  // ─── Database ───
  console.log('Database:');
  await test('Postgres connects', async () => {
    const result = await query('SELECT 1 as ok');
    assert(result.rows[0].ok === 1, 'Expected 1');
  });

  await test('Tables exist', async () => {
    const result = await query(
      `SELECT count(*) as cnt FROM information_schema.tables WHERE table_schema = 'public'`,
    );
    assert(parseInt(result.rows[0].cnt) >= 11, 'Expected at least 11 tables');
  });

  // ─── NATS ───
  console.log('\nNATS:');
  await test('NATS connects', async () => {
    const nc = await getNatsConnection();
    assert(!nc.isClosed(), 'Connection should be open');
  });

  await test('Create JetStream stream', async () => {
    await ensureStream('INBOX', [
      config.subjects.inboxRaw,
      config.subjects.inboxClassified,
      config.subjects.inboxReview,
    ]);
  });

  await test('Publish test message to NATS', async () => {
    await publish(config.subjects.inboxRaw, {
      rawText: 'Test message from pipeline test',
      source: 'test',
      senderId: 'test@pipeline',
      timestamp: new Date().toISOString(),
      messageId: 'test-001',
    });
  });

  // ─── Ollama ───
  console.log('\nOllama Classification:');

  const testMessages = [
    { text: 'I should build a mobile app for tracking daily habits', expected: 'idea' },
    { text: 'Fix the login bug on the dashboard before Friday', expected: 'task' },
    { text: 'The React team released v19 with server components', expected: 'note' },
    { text: 'How does Temporal handle workflow timeouts?', expected: 'question' },
  ];

  for (const msg of testMessages) {
    await test(`Classify: "${msg.text.substring(0, 40)}..." → expects ${msg.expected}`, async () => {
      const result = await classify(msg.text);
      assert(result.category !== undefined, 'Category should exist');
      assert(result.confidence >= 0 && result.confidence <= 1, 'Confidence should be 0-1');
      assert(result.summary.length > 0, 'Summary should not be empty');
      console.log(`    ${YELLOW}→ ${result.category} (${(result.confidence * 100).toFixed(0)}%) — ${result.summary}${NC}`);
    });
  }

  // ─── Embedding ───
  console.log('\nEmbeddings:');
  await test('Generate embedding', async () => {
    const embedding = await generateEmbedding('Test embedding for brain node');
    assert(Array.isArray(embedding), 'Should return array');
    assert(embedding.length === 768, `Expected 768 dimensions, got ${embedding.length}`);
  });

  // ─── Store Pipeline ───
  console.log('\nStore Pipeline (classify → store → audit):');
  await test('Full pipeline: classify → inbox → brain_node → audit', async () => {
    const testText = 'We should implement a WebSocket connection for real-time updates in the dashboard';
    const classification = await classify(testText);

    const inboxId = await createInboxItem(testText, 'test', classification, { test: true });
    assert(inboxId.length > 0, 'Inbox ID should exist');

    const brainNodeId = await createBrainNode(inboxId, testText, classification);
    assert(brainNodeId.length > 0, 'Brain node ID should exist');

    await logAudit({
      agentName: 'test',
      actionType: 'pipeline_test',
      riskClass: 'R0',
      inputSummary: testText.substring(0, 100),
      outputSummary: `${classification.category} (${classification.confidence})`,
      modelUsed: config.ollama.classifyModel,
    });

    // Verify data exists
    const inbox = await query('SELECT * FROM inbox_items WHERE id = $1', [inboxId]);
    assert(inbox.rows.length === 1, 'Inbox item should exist');
    assert(inbox.rows[0].classified_as === classification.category, 'Category should match');

    const brain = await query('SELECT * FROM brain_nodes WHERE id = $1', [brainNodeId]);
    assert(brain.rows.length === 1, 'Brain node should exist');
    assert(brain.rows[0].embedding !== null, 'Embedding should be stored');

    const audit = await query(
      "SELECT * FROM audit_events WHERE action_type = 'pipeline_test' ORDER BY timestamp DESC LIMIT 1",
    );
    assert(audit.rows.length === 1, 'Audit event should exist');

    console.log(`    ${YELLOW}→ Inbox: ${inboxId.substring(0, 8)}, Brain: ${brainNodeId.substring(0, 8)}, Audit logged${NC}`);
  });

  // ─── Summary ───
  console.log('\n═══════════════════════════════════════════');
  console.log(`  Results: ${GREEN}${passed} passed${NC}, ${RED}${failed} failed${NC}`);
  console.log('═══════════════════════════════════════════\n');

  await natsShutdown();
  await dbShutdown();

  process.exit(failed > 0 ? 1 : 0);
}

main().catch((err) => {
  console.error('Test fatal error:', err);
  process.exit(1);
});
