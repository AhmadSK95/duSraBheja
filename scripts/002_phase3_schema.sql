-- Phase 3: Multi-Agent + Safety Gates schema additions
-- Run: psql -d dusrabheja -f scripts/002_phase3_schema.sql

-- Approval requests table for agent approval gates
CREATE TABLE IF NOT EXISTS approval_requests (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    run_id          UUID NOT NULL REFERENCES agent_runs(id),
    risk_class      TEXT NOT NULL,
    summary         TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',  -- pending, approved, denied, timeout
    requested_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    responded_at    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_approval_requests_status ON approval_requests(status);
CREATE INDEX IF NOT EXISTS idx_approval_requests_run_id ON approval_requests(run_id);

-- Update seed agents: critic → Gemini, executor → Claude Sonnet
UPDATE agents SET provider = 'google', model = 'gemini-2.5-pro' WHERE name = 'critic';
UPDATE agents SET provider = 'anthropic', model = 'claude-sonnet-4-20250514' WHERE name = 'executor';
