import { query } from '../lib/db.js';
import { v4 as uuidv4 } from 'uuid';
import type { RiskClass, AgentRunStatus } from './types.js';

export interface AgentRun {
  id: string;
  agentName: string;
  taskDescription: string;
  taskType: string;
  status: AgentRunStatus;
  riskClass: RiskClass;
  modelUsed: string | null;
  tokensUsed: number | null;
  costUsd: number | null;
  durationMs: number | null;
  createdAt: Date;
  error: string | null;
}

export interface ApprovalRequest {
  id: string;
  runId: string;
  riskClass: string;
  summary: string;
  status: string;
  requestedAt: Date;
  respondedAt: Date | null;
}

export async function createAgentRun(
  agentName: string,
  task: string,
  taskType: string,
  riskClass: RiskClass,
  triggeredBy: string,
  input: object,
  parentRunId?: string,
): Promise<string> {
  // Look up agent_id by name
  const agentResult = await query<{ id: string }>(
    `SELECT id FROM agents WHERE name = $1 LIMIT 1`,
    [agentName],
  );
  const agentId = agentResult.rows[0]?.id ?? null;

  const id = uuidv4();
  await query(
    `INSERT INTO agent_runs (id, agent_id, task_description, task_type, status, risk_class, input_data, triggered_by, parent_run_id)
     VALUES ($1, $2, $3, $4, 'queued', $5, $6, $7, $8)`,
    [id, agentId, task, taskType, riskClass, JSON.stringify(input), triggeredBy, parentRunId ?? null],
  );
  return id;
}

export async function updateRunStatus(
  runId: string,
  status: AgentRunStatus,
  output?: object | null,
  error?: string | null,
  model?: string | null,
  tokens?: number | null,
  cost?: number | null,
  durationMs?: number | null,
): Promise<void> {
  const sets: string[] = ['status = $2'];
  const params: any[] = [runId, status];
  let idx = 3;

  if (output !== undefined) {
    sets.push(`output_data = $${idx}`);
    params.push(JSON.stringify(output));
    idx++;
  }
  if (error !== undefined) {
    sets.push(`error = $${idx}`);
    params.push(error);
    idx++;
  }
  if (model !== undefined) {
    sets.push(`model_used = $${idx}`);
    params.push(model);
    idx++;
  }
  if (tokens !== undefined) {
    sets.push(`tokens_used = $${idx}`);
    params.push(tokens);
    idx++;
  }
  if (cost !== undefined) {
    sets.push(`cost_usd = $${idx}`);
    params.push(cost);
    idx++;
  }
  if (durationMs !== undefined) {
    sets.push(`duration_ms = $${idx}`);
    params.push(durationMs);
    idx++;
  }

  if (status === 'running') {
    sets.push('started_at = NOW()');
  } else if (status === 'completed' || status === 'failed' || status === 'cancelled') {
    sets.push('completed_at = NOW()');
  }

  await query(`UPDATE agent_runs SET ${sets.join(', ')} WHERE id = $1`, params);
}

export async function getActiveRuns(): Promise<AgentRun[]> {
  const result = await query<any>(
    `SELECT ar.id, a.name as agent_name, ar.task_description, ar.task_type, ar.status,
            ar.risk_class, ar.model_used, ar.tokens_used, ar.cost_usd, ar.duration_ms,
            ar.created_at, ar.error
     FROM agent_runs ar
     LEFT JOIN agents a ON ar.agent_id = a.id
     WHERE ar.status IN ('queued', 'running', 'awaiting_approval')
     ORDER BY ar.created_at DESC`,
  );
  return result.rows.map((r: any) => ({
    id: r.id,
    agentName: r.agent_name ?? 'unknown',
    taskDescription: r.task_description,
    taskType: r.task_type,
    status: r.status,
    riskClass: r.risk_class,
    modelUsed: r.model_used,
    tokensUsed: r.tokens_used,
    costUsd: r.cost_usd,
    durationMs: r.duration_ms,
    createdAt: r.created_at,
    error: r.error,
  }));
}

export async function getRecentRuns(limit = 5): Promise<AgentRun[]> {
  const result = await query<any>(
    `SELECT ar.id, a.name as agent_name, ar.task_description, ar.task_type, ar.status,
            ar.risk_class, ar.model_used, ar.tokens_used, ar.cost_usd, ar.duration_ms,
            ar.created_at, ar.error
     FROM agent_runs ar
     LEFT JOIN agents a ON ar.agent_id = a.id
     ORDER BY ar.created_at DESC
     LIMIT $1`,
    [limit],
  );
  return result.rows.map((r: any) => ({
    id: r.id,
    agentName: r.agent_name ?? 'unknown',
    taskDescription: r.task_description,
    taskType: r.task_type,
    status: r.status,
    riskClass: r.risk_class,
    modelUsed: r.model_used,
    tokensUsed: r.tokens_used,
    costUsd: r.cost_usd,
    durationMs: r.duration_ms,
    createdAt: r.created_at,
    error: r.error,
  }));
}

export async function cancelAllRuns(): Promise<number> {
  const result = await query(
    `UPDATE agent_runs SET status = 'cancelled', completed_at = NOW()
     WHERE status IN ('queued', 'running', 'awaiting_approval')`,
  );
  return result.rowCount ?? 0;
}

export async function createApprovalRequest(
  runId: string,
  riskClass: string,
  summary: string,
): Promise<string> {
  const id = uuidv4();
  await query(
    `INSERT INTO approval_requests (id, run_id, risk_class, summary, status)
     VALUES ($1, $2, $3, $4, 'pending')`,
    [id, runId, riskClass, summary],
  );
  return id;
}

export async function getPendingApproval(): Promise<ApprovalRequest | null> {
  const result = await query<any>(
    `SELECT id, run_id, risk_class, summary, status, requested_at, responded_at
     FROM approval_requests
     WHERE status = 'pending'
     ORDER BY requested_at ASC
     LIMIT 1`,
  );
  if (result.rows.length === 0) return null;
  const r = result.rows[0];
  return {
    id: r.id,
    runId: r.run_id,
    riskClass: r.risk_class,
    summary: r.summary,
    status: r.status,
    requestedAt: r.requested_at,
    respondedAt: r.responded_at,
  };
}

export async function resolveApproval(
  id: string,
  decision: 'approved' | 'denied' | 'timeout',
): Promise<void> {
  await query(
    `UPDATE approval_requests SET status = $1, responded_at = NOW() WHERE id = $2`,
    [decision, id],
  );
}

export async function timeoutStaleApprovals(timeoutMinutes: number): Promise<number> {
  const result = await query(
    `UPDATE approval_requests SET status = 'timeout', responded_at = NOW()
     WHERE status = 'pending'
       AND requested_at < NOW() - INTERVAL '1 minute' * $1`,
    [timeoutMinutes],
  );
  return result.rowCount ?? 0;
}
