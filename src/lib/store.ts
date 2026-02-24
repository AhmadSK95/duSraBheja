import { query } from './db.js';
import { ClassificationResult, generateEmbedding } from './ollama-client.js';

export interface InboxItem {
  id: string;
  rawText: string;
  source: string;
  classifiedAs: string | null;
  confidence: number | null;
  projectId: string | null;
  priority: string;
  nextAction: string | null;
  status: string;
  createdAt: Date;
}

export interface BrainNode {
  id: string;
  title: string;
  content: string | null;
  nodeType: string;
  category: string | null;
  priority: string;
  status: string;
  nextAction: string | null;
  sourceInboxId: string | null;
}

export async function createInboxItem(
  rawText: string,
  source: string,
  classification: ClassificationResult | null,
  sourceMetadata?: Record<string, any>,
): Promise<string> {
  const status = classification
    ? classification.confidence >= 0.7
      ? 'classified'
      : 'review'
    : 'pending';

  const result = await query<{ id: string }>(
    `INSERT INTO inbox_items (raw_text, source, source_metadata, classified_as, confidence, priority, next_action, status)
     VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
     RETURNING id`,
    [
      rawText,
      source,
      JSON.stringify(sourceMetadata || {}),
      classification?.category || null,
      classification?.confidence || null,
      classification?.priority || 'medium',
      classification?.nextAction || null,
      status,
    ],
  );
  return result.rows[0].id;
}

export async function createBrainNode(
  inboxId: string,
  rawText: string,
  classification: ClassificationResult,
): Promise<string> {
  // Generate embedding for semantic search
  let embedding: number[] | null = null;
  try {
    embedding = await generateEmbedding(rawText);
  } catch (err) {
    console.warn('[Store] Failed to generate embedding:', (err as Error).message);
  }

  const embeddingStr = embedding ? `[${embedding.join(',')}]` : null;

  const result = await query<{ id: string }>(
    `INSERT INTO brain_nodes (title, content, node_type, priority, next_action, source_inbox_id, embedding)
     VALUES ($1, $2, $3, $4, $5, $6, $7::vector)
     RETURNING id`,
    [
      classification.summary,
      rawText,
      classification.category,
      classification.priority,
      classification.nextAction || null,
      inboxId,
      embeddingStr,
    ],
  );
  return result.rows[0].id;
}

export async function getInboxItem(id: string): Promise<InboxItem | null> {
  const result = await query<any>(
    `SELECT id, raw_text, source, classified_as, confidence, project_id, priority, next_action, status, created_at
     FROM inbox_items WHERE id = $1`,
    [id],
  );
  if (result.rows.length === 0) return null;
  const r = result.rows[0];
  return {
    id: r.id,
    rawText: r.raw_text,
    source: r.source,
    classifiedAs: r.classified_as,
    confidence: r.confidence,
    projectId: r.project_id,
    priority: r.priority,
    nextAction: r.next_action,
    status: r.status,
    createdAt: r.created_at,
  };
}

export async function getTodayItems(): Promise<InboxItem[]> {
  const result = await query<any>(
    `SELECT id, raw_text, source, classified_as, confidence, project_id, priority, next_action, status, created_at
     FROM inbox_items
     WHERE created_at >= CURRENT_DATE
     ORDER BY created_at DESC`,
  );
  return result.rows.map((r: any) => ({
    id: r.id,
    rawText: r.raw_text,
    source: r.source,
    classifiedAs: r.classified_as,
    confidence: r.confidence,
    projectId: r.project_id,
    priority: r.priority,
    nextAction: r.next_action,
    status: r.status,
    createdAt: r.created_at,
  }));
}

export async function getReviewQueue(): Promise<InboxItem[]> {
  const result = await query<any>(
    `SELECT id, raw_text, source, classified_as, confidence, project_id, priority, next_action, status, created_at
     FROM inbox_items
     WHERE status = 'review'
     ORDER BY created_at DESC
     LIMIT 50`,
  );
  return result.rows.map((r: any) => ({
    id: r.id,
    rawText: r.raw_text,
    source: r.source,
    classifiedAs: r.classified_as,
    confidence: r.confidence,
    projectId: r.project_id,
    priority: r.priority,
    nextAction: r.next_action,
    status: r.status,
    createdAt: r.created_at,
  }));
}

export async function applyCorrection(
  inboxId: string,
  field: string,
  oldValue: string | null,
  newValue: string,
): Promise<void> {
  // Log the correction
  await query(
    `INSERT INTO corrections (entity_type, entity_id, field_name, old_value, new_value)
     VALUES ('inbox_item', $1, $2, $3, $4)`,
    [inboxId, field, oldValue, newValue],
  );

  // Apply to inbox_items
  if (field === 'classified_as') {
    await query(
      `UPDATE inbox_items SET classified_as = $1, status = 'classified', updated_at = NOW() WHERE id = $2`,
      [newValue, inboxId],
    );
  }
}
