import { StateGraph, Annotation, START, END } from '@langchain/langgraph';
import { v4 as uuidv4 } from 'uuid';
import { generatePlan } from './planner.js';
import { reviewPlan } from './critic.js';
import { executePlan } from './executor.js';
import { evaluateAndLog } from './sentinel.js';
import {
  formatPlanForApproval,
  formatExecutionResult,
  formatDenial,
  formatError,
  sendToWhatsApp,
} from './narrator.js';
import { storyboardFromAgentRun } from './storyboard.js';
import {
  createApprovalRequest,
  getPendingApproval,
  resolveApproval,
  updateRunStatus,
} from './agent-store.js';
import { isLockdown } from './lockdown.js';
import { config } from '../lib/config.js';
import type {
  AgentGraphState,
  AgentContext,
  PlannerOutput,
  CriticOutput,
  RiskClass,
} from './types.js';
import { createInitialState } from './types.js';

// ─── LangGraph State Annotation ─────────────────────────────────────────────

const GraphState = Annotation.Root({
  taskDescription: Annotation<string>,
  context: Annotation<AgentContext>,
  autoExecute: Annotation<boolean>,
  plan: Annotation<PlannerOutput | null>,
  planRunId: Annotation<string | null>,
  planTokens: Annotation<number>,
  planCost: Annotation<number>,
  review: Annotation<CriticOutput | null>,
  criticRunId: Annotation<string | null>,
  criticTokens: Annotation<number>,
  criticCost: Annotation<number>,
  sentinelDecision: Annotation<string | null>,
  sentinelReason: Annotation<string | null>,
  selectedOptionIndex: Annotation<number>,
  approvalStatus: Annotation<string | null>,
  approvalRequestId: Annotation<string | null>,
  executionResult: Annotation<{ result: string; actions: string[]; artifacts: string[] } | null>,
  executorRunId: Annotation<string | null>,
  executorTokens: Annotation<number>,
  executorCost: Annotation<number>,
  totalTokens: Annotation<number>,
  totalCost: Annotation<number>,
  totalDurationMs: Annotation<number>,
  error: Annotation<string | null>,
  currentStep: Annotation<string>,
});

// ─── Node Functions ─────────────────────────────────────────────────────────

async function planNode(state: typeof GraphState.State): Promise<Partial<typeof GraphState.State>> {
  if (isLockdown()) {
    return { error: 'LOCKDOWN: All agent operations suspended', currentStep: 'error' };
  }

  try {
    const { plan, runId, tokens, cost } = await generatePlan(
      state.taskDescription,
      state.context,
    );

    return {
      plan,
      planRunId: runId,
      planTokens: tokens,
      planCost: cost,
      totalTokens: state.totalTokens + tokens,
      totalCost: state.totalCost + cost,
      currentStep: 'critic',
    };
  } catch (err) {
    return { error: `Planner failed: ${(err as Error).message}`, currentStep: 'error' };
  }
}

async function criticNode(state: typeof GraphState.State): Promise<Partial<typeof GraphState.State>> {
  if (!state.plan) {
    return { error: 'No plan to review', currentStep: 'error' };
  }

  try {
    const { review, runId, tokens, cost } = await reviewPlan(
      state.plan,
      state.taskDescription,
      state.context,
    );

    return {
      review,
      criticRunId: runId,
      criticTokens: tokens,
      criticCost: cost,
      totalTokens: state.totalTokens + tokens,
      totalCost: state.totalCost + cost,
      selectedOptionIndex: state.plan.recommendedIndex,
      currentStep: 'sentinel',
    };
  } catch (err) {
    return { error: `Critic failed: ${(err as Error).message}`, currentStep: 'error' };
  }
}

async function sentinelNode(state: typeof GraphState.State): Promise<Partial<typeof GraphState.State>> {
  if (!state.plan) {
    return { error: 'No plan for sentinel check', currentStep: 'error' };
  }

  const selectedOption = state.plan.options[state.selectedOptionIndex];
  const riskClass = (selectedOption?.riskClass || 'R1') as RiskClass;

  const result = await evaluateAndLog(
    'executor',
    'execute_plan',
    null,
    riskClass,
    state.context.traceId,
  );

  return {
    sentinelDecision: result.decision,
    sentinelReason: result.reason,
    currentStep: 'route_sentinel',
  };
}

async function approvalGateNode(state: typeof GraphState.State): Promise<Partial<typeof GraphState.State>> {
  if (!state.plan || !state.review) {
    return { error: 'Missing plan or review for approval', currentStep: 'error' };
  }

  // Send approval request to WhatsApp
  const message = formatPlanForApproval(
    state.plan,
    state.review,
    state.taskDescription,
    state.planRunId || '',
  );
  await sendToWhatsApp(state.context.chatId, message);

  // Create approval request in DB
  const selectedOption = state.plan.options[state.selectedOptionIndex];
  const approvalId = await createApprovalRequest(
    state.planRunId || state.context.traceId,
    selectedOption?.riskClass || 'R2',
    `Execute: ${state.taskDescription.substring(0, 100)}`,
  );

  // Update planner run to awaiting_approval
  if (state.planRunId) {
    await updateRunStatus(state.planRunId, 'awaiting_approval');
  }

  // Poll for approval (with timeout)
  const timeoutMs = config.agents.approvalTimeoutMinutes * 60 * 1000;
  const pollIntervalMs = 3000;
  const startTime = Date.now();

  let approvalStatus: string = 'timeout';

  while (Date.now() - startTime < timeoutMs) {
    await new Promise((resolve) => setTimeout(resolve, pollIntervalMs));

    const pending = await getPendingApproval();
    if (!pending || pending.id !== approvalId) {
      // Approval was resolved (either by user or by timeout poller)
      const { query } = await import('../lib/db.js');
      const result = await query<{ status: string }>(
        `SELECT status FROM approval_requests WHERE id = $1`,
        [approvalId],
      );
      if (result.rows.length > 0 && result.rows[0].status !== 'pending') {
        approvalStatus = result.rows[0].status;
        break;
      }
    }

    // Check for lockdown during wait
    if (isLockdown()) {
      await resolveApproval(approvalId, 'denied');
      approvalStatus = 'denied';
      break;
    }
  }

  // If still pending after loop, timeout it
  if (approvalStatus === 'timeout') {
    await resolveApproval(approvalId, 'timeout');
  }

  return {
    approvalStatus,
    approvalRequestId: approvalId,
    currentStep: `route_approval`,
  };
}

async function executeNode(state: typeof GraphState.State): Promise<Partial<typeof GraphState.State>> {
  if (!state.plan) {
    return { error: 'No plan to execute', currentStep: 'error' };
  }

  try {
    const { result, runId, tokens, cost } = await executePlan(
      state.plan,
      state.selectedOptionIndex,
      state.taskDescription,
      state.context,
    );

    return {
      executionResult: result,
      executorRunId: runId,
      executorTokens: tokens,
      executorCost: cost,
      totalTokens: state.totalTokens + tokens,
      totalCost: state.totalCost + cost,
      currentStep: 'narrate',
    };
  } catch (err) {
    return { error: `Executor failed: ${(err as Error).message}`, currentStep: 'error' };
  }
}

async function narrateNode(state: typeof GraphState.State): Promise<Partial<typeof GraphState.State>> {
  if (state.executionResult) {
    const message = formatExecutionResult(
      state.executionResult,
      state.taskDescription,
      state as unknown as AgentGraphState,
    );
    await sendToWhatsApp(state.context.chatId, message);
  }
  return { currentStep: 'storyboard' };
}

async function narrateDenialNode(state: typeof GraphState.State): Promise<Partial<typeof GraphState.State>> {
  const reason = state.sentinelDecision === 'deny'
    ? `Sentinel denied: ${state.sentinelReason}`
    : state.approvalStatus === 'denied'
      ? 'User denied the plan'
      : state.approvalStatus === 'timeout'
        ? 'Approval timed out'
        : state.error || 'Unknown reason';

  const message = formatDenial(state.taskDescription, reason, state as unknown as AgentGraphState);
  await sendToWhatsApp(state.context.chatId, message);
  return { currentStep: 'storyboard' };
}

async function storyboardNode(state: typeof GraphState.State): Promise<Partial<typeof GraphState.State>> {
  try {
    await storyboardFromAgentRun(state as unknown as AgentGraphState, state.context.chatId);
  } catch (err) {
    console.warn('[Graph] Storyboard generation failed (non-fatal):', (err as Error).message);
  }

  const totalDurationMs = Date.now(); // Will be computed by caller
  return { currentStep: 'done' };
}

async function errorNode(state: typeof GraphState.State): Promise<Partial<typeof GraphState.State>> {
  const message = formatError(state.taskDescription, state.error || 'Unknown error');
  await sendToWhatsApp(state.context.chatId, message);
  return { currentStep: 'done' };
}

// ─── Routing Functions ──────────────────────────────────────────────────────

function routeAfterSentinel(state: typeof GraphState.State): string {
  if (state.error) return 'error_handler';

  if (state.sentinelDecision === 'deny') return 'narrate_denial';

  if (state.sentinelDecision === 'allow') {
    if (state.autoExecute) return 'execute';
    // Even auto-allowed, if not autoExecute mode, show plan for approval
    return 'approval_gate';
  }

  if (state.sentinelDecision === 'require_approval') return 'approval_gate';

  return 'error_handler';
}

function routeAfterApproval(state: typeof GraphState.State): string {
  if (state.approvalStatus === 'approved') return 'execute';
  return 'narrate_denial';
}

function routeAfterPlan(state: typeof GraphState.State): string {
  if (state.error) return 'error_handler';
  return 'critic';
}

function routeAfterCritic(state: typeof GraphState.State): string {
  if (state.error) return 'error_handler';
  // If critic rejects AND score < 3, deny immediately
  if (state.review && !state.review.approved && state.review.score < 3) {
    return 'narrate_denial';
  }
  return 'sentinel';
}

// ─── Build Graph ────────────────────────────────────────────────────────────

export function buildAgentGraph() {
  const graph = new StateGraph(GraphState)
    .addNode('planner', planNode)
    .addNode('critic', criticNode)
    .addNode('sentinel', sentinelNode)
    .addNode('approval_gate', approvalGateNode)
    .addNode('execute', executeNode)
    .addNode('narrate', narrateNode)
    .addNode('narrate_denial', narrateDenialNode)
    .addNode('storyboard', storyboardNode)
    .addNode('error_handler', errorNode)
    // Edges
    .addEdge(START, 'planner')
    .addConditionalEdges('planner', routeAfterPlan)
    .addConditionalEdges('critic', routeAfterCritic)
    .addConditionalEdges('sentinel', routeAfterSentinel)
    .addConditionalEdges('approval_gate', routeAfterApproval)
    .addEdge('execute', 'narrate')
    .addEdge('narrate', 'storyboard')
    .addEdge('narrate_denial', 'storyboard')
    .addEdge('storyboard', END)
    .addEdge('error_handler', END);

  return graph.compile();
}

// ─── Public API ─────────────────────────────────────────────────────────────

let compiledGraph: ReturnType<typeof buildAgentGraph> | null = null;

export async function runAgentChain(
  taskDescription: string,
  context: AgentContext,
  autoExecute: boolean,
): Promise<AgentGraphState> {
  if (isLockdown()) {
    const state = createInitialState(taskDescription, context, autoExecute);
    state.error = 'LOCKDOWN: All agent operations suspended';
    const message = formatError(taskDescription, state.error);
    await sendToWhatsApp(context.chatId, message);
    return state;
  }

  if (!compiledGraph) {
    compiledGraph = buildAgentGraph();
  }

  const startTime = Date.now();

  const initialState = createInitialState(taskDescription, context, autoExecute);

  const result = await compiledGraph.invoke(initialState);

  // Compute total duration
  const finalState = {
    ...initialState,
    ...result,
    totalDurationMs: Date.now() - startTime,
  } as AgentGraphState;

  return finalState;
}
