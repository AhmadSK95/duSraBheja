export type RiskClass = 'R0' | 'R1' | 'R2' | 'R3' | 'R4';

export type AgentRunStatus =
  | 'queued'
  | 'running'
  | 'awaiting_approval'
  | 'completed'
  | 'failed'
  | 'cancelled';

export interface PlanOption {
  label: string;
  steps: string[];
  riskClass: RiskClass;
  estimatedCostUsd: number;
  rationale: string;
}

export interface PlannerOutput {
  options: PlanOption[];
  recommendedIndex: number;
  reasoning: string;
}

export interface CriticOutput {
  approved: boolean;
  score: number; // 0-10
  issues: string[];
  suggestions: string[];
  reasoning: string;
}

export type SentinelDecision = 'allow' | 'require_approval' | 'deny';

export interface SentinelResult {
  decision: SentinelDecision;
  matchedRule: string | null;
  reason: string;
}

export interface ExecutorOutput {
  result: string;
  actions: string[];
  artifacts: string[];
}

export interface AgentContext {
  chatId: string;
  traceId: string;
  triggeredBy: string;
}

export interface AgentGraphState {
  // Input
  taskDescription: string;
  context: AgentContext;
  autoExecute: boolean;

  // Planner output
  plan: PlannerOutput | null;
  planRunId: string | null;
  planTokens: number;
  planCost: number;

  // Critic output
  review: CriticOutput | null;
  criticRunId: string | null;
  criticTokens: number;
  criticCost: number;

  // Sentinel output
  sentinelDecision: SentinelDecision | null;
  sentinelReason: string | null;
  selectedOptionIndex: number;

  // Approval
  approvalStatus: 'pending' | 'approved' | 'denied' | 'timeout' | 'not_needed' | null;
  approvalRequestId: string | null;

  // Executor output
  executionResult: ExecutorOutput | null;
  executorRunId: string | null;
  executorTokens: number;
  executorCost: number;

  // Aggregate
  totalTokens: number;
  totalCost: number;
  totalDurationMs: number;
  error: string | null;
  currentStep: string;
}

export function createInitialState(
  taskDescription: string,
  context: AgentContext,
  autoExecute: boolean,
): AgentGraphState {
  return {
    taskDescription,
    context,
    autoExecute,
    plan: null,
    planRunId: null,
    planTokens: 0,
    planCost: 0,
    review: null,
    criticRunId: null,
    criticTokens: 0,
    criticCost: 0,
    sentinelDecision: null,
    sentinelReason: null,
    selectedOptionIndex: 0,
    approvalStatus: null,
    approvalRequestId: null,
    executionResult: null,
    executorRunId: null,
    executorTokens: 0,
    executorCost: 0,
    totalTokens: 0,
    totalCost: 0,
    totalDurationMs: 0,
    error: null,
    currentStep: 'start',
  };
}
