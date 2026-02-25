import { query } from '../lib/db.js';
import { logAudit } from '../lib/audit.js';
import type { RiskClass, SentinelResult, SentinelDecision } from './types.js';

interface PolicyRule {
  id: string;
  name: string;
  ruleType: string; // allow, deny, require_approval
  agentPattern: string | null;
  actionPattern: string | null;
  toolPattern: string | null;
  riskClass: RiskClass | null;
  isActive: boolean;
  priority: number;
}

let cachedPolicies: PolicyRule[] | null = null;
let cacheTimestamp = 0;
const CACHE_TTL_MS = 5 * 60 * 1000; // 5 minutes

export async function loadPolicies(): Promise<PolicyRule[]> {
  const now = Date.now();
  if (cachedPolicies && now - cacheTimestamp < CACHE_TTL_MS) {
    return cachedPolicies;
  }

  const result = await query<any>(
    `SELECT id, name, rule_type, agent_pattern, action_pattern, tool_pattern, risk_class, is_active, priority
     FROM policy_rules
     WHERE is_active = true
     ORDER BY priority ASC`,
  );

  cachedPolicies = result.rows.map((r: any) => ({
    id: r.id,
    name: r.name,
    ruleType: r.rule_type,
    agentPattern: r.agent_pattern,
    actionPattern: r.action_pattern,
    toolPattern: r.tool_pattern,
    riskClass: r.risk_class,
    isActive: r.is_active,
    priority: r.priority,
  }));
  cacheTimestamp = now;
  return cachedPolicies;
}

export function clearPolicyCache(): void {
  cachedPolicies = null;
  cacheTimestamp = 0;
}

function matchesPattern(value: string | null, pattern: string | null): boolean {
  if (!pattern) return true; // null pattern matches everything
  if (!value) return false;
  // Simple wildcard matching: * matches anything
  if (pattern === '*') return true;
  return value.toLowerCase() === pattern.toLowerCase();
}

export function evaluate(
  agentName: string,
  action: string,
  tool: string | null,
  riskClass: RiskClass,
): SentinelResult {
  // FAIL-CLOSED: if no policies loaded, deny all
  if (!cachedPolicies || cachedPolicies.length === 0) {
    return {
      decision: 'deny',
      matchedRule: null,
      reason: 'FAIL-CLOSED: No policies loaded',
    };
  }

  // Find matching rules (sorted by priority — lowest number = highest priority)
  for (const rule of cachedPolicies) {
    const matchesAgent = matchesPattern(agentName, rule.agentPattern);
    const matchesAction = matchesPattern(action, rule.actionPattern);
    const matchesTool = matchesPattern(tool, rule.toolPattern);
    const matchesRisk = !rule.riskClass || rule.riskClass === riskClass;

    if (matchesAgent && matchesAction && matchesTool && matchesRisk) {
      return {
        decision: rule.ruleType as SentinelDecision,
        matchedRule: rule.name,
        reason: `Matched policy: ${rule.name} (priority ${rule.priority})`,
      };
    }
  }

  // Default: FAIL-CLOSED — deny if no rule matches
  return {
    decision: 'deny',
    matchedRule: null,
    reason: 'FAIL-CLOSED: No matching policy rule',
  };
}

export async function evaluateAndLog(
  agentName: string,
  action: string,
  tool: string | null,
  riskClass: RiskClass,
  traceId: string,
): Promise<SentinelResult> {
  try {
    // Ensure policies are loaded
    await loadPolicies();
    const result = evaluate(agentName, action, tool, riskClass);

    await logAudit(
      {
        agentName: 'sentinel',
        actionType: 'policy_check',
        riskClass,
        toolName: tool ?? undefined,
        inputSummary: `${agentName}:${action} (${riskClass})`,
        outputSummary: `${result.decision}: ${result.reason}`,
        decision: result.decision,
      },
      traceId,
    );

    return result;
  } catch (err) {
    // FAIL-CLOSED: any error → deny everything
    const errorResult: SentinelResult = {
      decision: 'deny',
      matchedRule: null,
      reason: `FAIL-CLOSED ERROR: ${(err as Error).message}`,
    };

    try {
      await logAudit(
        {
          agentName: 'sentinel',
          actionType: 'policy_check',
          riskClass,
          error: (err as Error).message,
          decision: 'deny',
        },
        traceId,
      );
    } catch {
      // Even audit logging failed — still deny
      console.error('[Sentinel] CRITICAL: audit logging failed during error handling');
    }

    return errorResult;
  }
}
