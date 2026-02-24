-- duSraBheja: Initial Schema Migration
-- Phase 0: Foundation tables for the Fractal Brain Mesh
-- Date: 2026-02-24

-- Enable extensions
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================================
-- INBOX ITEMS (The Drop Box)
-- Raw captured items from WhatsApp or other sources
-- ============================================================
CREATE TABLE inbox_items (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    raw_text        TEXT NOT NULL,
    source          TEXT NOT NULL DEFAULT 'whatsapp',
    source_metadata JSONB DEFAULT '{}',
    classified_as   TEXT,                          -- idea, task, note, question, link, voice
    confidence      REAL,                          -- 0.0 to 1.0
    project_id      UUID,
    priority        TEXT DEFAULT 'medium',         -- low, medium, high, urgent
    next_action     TEXT,
    status          TEXT NOT NULL DEFAULT 'pending', -- pending, classified, review, processed, archived
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    processed_at    TIMESTAMPTZ
);

CREATE INDEX idx_inbox_status ON inbox_items(status);
CREATE INDEX idx_inbox_created ON inbox_items(created_at DESC);
CREATE INDEX idx_inbox_project ON inbox_items(project_id);

-- ============================================================
-- BRAIN NODES (The Filing Cabinet + Semantic Memory)
-- Structured knowledge items derived from inbox or created directly
-- ============================================================
CREATE TABLE brain_nodes (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    title           TEXT NOT NULL,
    content         TEXT,
    node_type       TEXT NOT NULL,                 -- idea, task, note, reference, project, goal, principle
    category        TEXT,
    tags            TEXT[] DEFAULT '{}',
    priority        TEXT DEFAULT 'medium',
    status          TEXT NOT NULL DEFAULT 'active', -- active, completed, archived, stale
    next_action     TEXT,
    source_inbox_id UUID REFERENCES inbox_items(id),
    project_id      UUID,
    parent_id       UUID REFERENCES brain_nodes(id),
    embedding       vector(768),                   -- nomic-embed-text dimension
    metadata        JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_accessed   TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_brain_type ON brain_nodes(node_type);
CREATE INDEX idx_brain_status ON brain_nodes(status);
CREATE INDEX idx_brain_project ON brain_nodes(project_id);
CREATE INDEX idx_brain_parent ON brain_nodes(parent_id);
CREATE INDEX idx_brain_tags ON brain_nodes USING GIN(tags);
CREATE INDEX idx_brain_embedding ON brain_nodes USING hnsw (embedding vector_cosine_ops);

-- ============================================================
-- BRAIN EDGES (Graph relationships between nodes)
-- Replaces Apache AGE with simple junction table
-- ============================================================
CREATE TABLE brain_edges (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source_id       UUID NOT NULL REFERENCES brain_nodes(id) ON DELETE CASCADE,
    target_id       UUID NOT NULL REFERENCES brain_nodes(id) ON DELETE CASCADE,
    relation_type   TEXT NOT NULL,                 -- related_to, blocks, subtask_of, derived_from, contradicts, supports
    weight          REAL DEFAULT 1.0,
    metadata        JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(source_id, target_id, relation_type)
);

CREATE INDEX idx_edges_source ON brain_edges(source_id);
CREATE INDEX idx_edges_target ON brain_edges(target_id);
CREATE INDEX idx_edges_type ON brain_edges(relation_type);

-- ============================================================
-- PROJECTS
-- Registered codebases and project containers
-- ============================================================
CREATE TABLE projects (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name            TEXT NOT NULL UNIQUE,
    description     TEXT,
    github_repo     TEXT,                          -- owner/repo format
    local_path      TEXT,
    is_active       BOOLEAN NOT NULL DEFAULT true,
    metadata        JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================
-- REPO STATUS (GitHub snapshot per project)
-- ============================================================
CREATE TABLE repo_status (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    project_id      UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    current_branch  TEXT,
    open_prs        INTEGER DEFAULT 0,
    open_issues     INTEGER DEFAULT 0,
    failing_checks  INTEGER DEFAULT 0,
    stale_branches  INTEGER DEFAULT 0,
    recent_commits  JSONB DEFAULT '[]',
    pr_details      JSONB DEFAULT '[]',
    issue_details   JSONB DEFAULT '[]',
    last_synced     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_repo_project ON repo_status(project_id);
CREATE INDEX idx_repo_synced ON repo_status(last_synced DESC);

-- ============================================================
-- AGENTS (Agent Registry)
-- ============================================================
CREATE TABLE agents (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name            TEXT NOT NULL UNIQUE,
    agent_type      TEXT NOT NULL,                 -- llm, code, tool, custom
    provider        TEXT,                          -- anthropic, openai, google, local, custom
    model           TEXT,                          -- claude-sonnet-4-20250514, gpt-4o, llama3.1:8b
    api_endpoint    TEXT,
    tool_permissions TEXT[] DEFAULT '{}',
    max_risk_level  TEXT NOT NULL DEFAULT 'R1',    -- R0, R1, R2, R3, R4
    config          JSONB DEFAULT '{}',
    is_active       BOOLEAN NOT NULL DEFAULT true,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================
-- AGENT RUNS (Execution log)
-- ============================================================
CREATE TABLE agent_runs (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    agent_id        UUID NOT NULL REFERENCES agents(id),
    task_description TEXT NOT NULL,
    task_type       TEXT,                          -- classify, plan, review, execute, summarize
    status          TEXT NOT NULL DEFAULT 'queued', -- queued, running, awaiting_approval, completed, failed, cancelled
    risk_class      TEXT NOT NULL DEFAULT 'R0',
    input_data      JSONB DEFAULT '{}',
    output_data     JSONB DEFAULT '{}',
    error           TEXT,
    model_used      TEXT,
    tokens_used     INTEGER,
    cost_usd        NUMERIC(10,6),
    duration_ms     INTEGER,
    triggered_by    TEXT DEFAULT 'user',           -- user, system, cron, agent
    parent_run_id   UUID REFERENCES agent_runs(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at      TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ
);

CREATE INDEX idx_runs_agent ON agent_runs(agent_id);
CREATE INDEX idx_runs_status ON agent_runs(status);
CREATE INDEX idx_runs_created ON agent_runs(created_at DESC);

-- ============================================================
-- POLICY RULES (Safety & Governance)
-- ============================================================
CREATE TABLE policy_rules (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name            TEXT NOT NULL UNIQUE,
    description     TEXT,
    rule_type       TEXT NOT NULL,                 -- allow, deny, require_approval
    agent_pattern   TEXT DEFAULT '*',              -- glob pattern for agent names
    action_pattern  TEXT DEFAULT '*',              -- glob pattern for action types
    tool_pattern    TEXT DEFAULT '*',              -- glob pattern for tool names
    risk_class      TEXT,                          -- applies to this risk class
    conditions      JSONB DEFAULT '{}',
    is_active       BOOLEAN NOT NULL DEFAULT true,
    priority        INTEGER NOT NULL DEFAULT 100,  -- lower = higher priority
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_policy_active ON policy_rules(is_active, priority);

-- ============================================================
-- AUDIT EVENTS (The Receipt - immutable log)
-- ============================================================
CREATE TABLE audit_events (
    id              BIGSERIAL PRIMARY KEY,
    timestamp       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    trace_id        UUID NOT NULL DEFAULT uuid_generate_v4(),
    agent_name      TEXT NOT NULL,
    action_type     TEXT NOT NULL,
    risk_class      TEXT NOT NULL DEFAULT 'R0',
    tool_name       TEXT,
    input_summary   TEXT,
    output_summary  TEXT,
    decision        TEXT,                          -- approved, denied, auto_approved, escalated
    policy_rule_id  UUID REFERENCES policy_rules(id),
    model_used      TEXT,
    tokens_used     INTEGER,
    cost_usd        NUMERIC(10,6),
    duration_ms     INTEGER,
    error           TEXT,
    metadata        JSONB DEFAULT '{}'
);

CREATE INDEX idx_audit_timestamp ON audit_events(timestamp DESC);
CREATE INDEX idx_audit_agent ON audit_events(agent_name);
CREATE INDEX idx_audit_trace ON audit_events(trace_id);
CREATE INDEX idx_audit_risk ON audit_events(risk_class);

-- ============================================================
-- NUDGES (Tap on the Shoulder)
-- ============================================================
CREATE TABLE nudges (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    nudge_type      TEXT NOT NULL,                 -- daily_summary, stale_reminder, action_prompt, correction_request
    target_node_id  UUID REFERENCES brain_nodes(id),
    message         TEXT NOT NULL,
    channel         TEXT NOT NULL DEFAULT 'whatsapp', -- whatsapp, ui, both
    status          TEXT NOT NULL DEFAULT 'pending',  -- pending, sent, acknowledged, dismissed
    scheduled_for   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    sent_at         TIMESTAMPTZ,
    acknowledged_at TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_nudges_status ON nudges(status, scheduled_for);

-- ============================================================
-- CORRECTIONS (The Fix Button - learning from user feedback)
-- ============================================================
CREATE TABLE corrections (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_type     TEXT NOT NULL,                 -- inbox_item, brain_node
    entity_id       UUID NOT NULL,
    field_name      TEXT NOT NULL,                 -- classified_as, category, priority, etc.
    old_value       TEXT,
    new_value       TEXT NOT NULL,
    reason          TEXT,
    applied_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_corrections_entity ON corrections(entity_type, entity_id);
CREATE INDEX idx_corrections_field ON corrections(field_name);

-- ============================================================
-- SEED DATA
-- ============================================================

-- Register default agents
INSERT INTO agents (name, agent_type, provider, model, max_risk_level, tool_permissions) VALUES
    ('classifier', 'llm', 'local', 'llama3.1:8b', 'R0', ARRAY['classify']),
    ('planner', 'llm', 'anthropic', 'claude-sonnet-4-20250514', 'R1', ARRAY['plan', 'search', 'read']),
    ('critic', 'llm', 'anthropic', 'claude-sonnet-4-20250514', 'R0', ARRAY['review']),
    ('executor', 'llm', 'openai', 'gpt-4o', 'R2', ARRAY['execute', 'code', 'git']),
    ('narrator', 'llm', 'local', 'llama3.1:8b', 'R0', ARRAY['summarize']),
    ('sentinel', 'llm', 'anthropic', 'claude-sonnet-4-20250514', 'R0', ARRAY['validate']),
    ('scheduler', 'llm', 'local', 'llama3.1:8b', 'R0', ARRAY['schedule', 'nudge']);

-- Register the duSraBheja project itself
INSERT INTO projects (name, description, github_repo, local_path) VALUES
    ('duSraBheja', 'Solo AI Command Center - Second Brain + Multi-Agent Swarm', 'AhmadSK95/duSraBheja', '/Users/moenuddeenahmadshaik/Desktop/duSraBheja');

-- Default policy rules
INSERT INTO policy_rules (name, description, rule_type, risk_class, priority) VALUES
    ('auto_approve_r0', 'Auto-approve all R0 (read-only) actions', 'allow', 'R0', 10),
    ('auto_approve_r1', 'Auto-approve R1 (local write) actions', 'allow', 'R1', 20),
    ('require_approval_r2', 'Require approval for R2 (external write) actions', 'require_approval', 'R2', 30),
    ('require_approval_r3', 'Require approval for R3 (destructive) actions', 'require_approval', 'R3', 40),
    ('deny_r4_default', 'Deny R4 (critical) actions by default', 'deny', 'R4', 50);
