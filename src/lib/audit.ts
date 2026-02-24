import { query } from './db.js';
import { v4 as uuidv4 } from 'uuid';

export interface AuditEntry {
  agentName: string;
  actionType: string;
  riskClass?: string;
  toolName?: string;
  inputSummary?: string;
  outputSummary?: string;
  decision?: string;
  modelUsed?: string;
  tokensUsed?: number;
  costUsd?: number;
  durationMs?: number;
  error?: string;
  metadata?: Record<string, any>;
}

export async function logAudit(entry: AuditEntry, traceId?: string): Promise<void> {
  const trace = traceId || uuidv4();
  await query(
    `INSERT INTO audit_events
     (trace_id, agent_name, action_type, risk_class, tool_name, input_summary, output_summary, decision, model_used, tokens_used, cost_usd, duration_ms, error, metadata)
     VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)`,
    [
      trace,
      entry.agentName,
      entry.actionType,
      entry.riskClass || 'R0',
      entry.toolName || null,
      entry.inputSummary || null,
      entry.outputSummary || null,
      entry.decision || 'auto_approved',
      entry.modelUsed || null,
      entry.tokensUsed || null,
      entry.costUsd || null,
      entry.durationMs || null,
      entry.error || null,
      entry.metadata ? JSON.stringify(entry.metadata) : '{}',
    ],
  );
}
