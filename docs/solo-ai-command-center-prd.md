# Product Requirements Document (PRD)
## Product
Solo AI Command Center (WhatsApp-first Second Brain + Multi-Agent Workspace)

## Document Info
- Version: v0.1
- Date: 2026-02-24
- Type: Product requirements (PM view)

## 1. Background
The product is a solo-user system that converts passive information storage into an active operating system. The user can capture from WhatsApp, see a visual storyboard of their "brain," work across codebases, monitor multiple GitHub projects, and orchestrate multiple AI agents (Claude, Antigravity, Codex, others) from one control surface.

## 2. Product Vision
One personal command center where a solo user can:
- Capture everything through WhatsApp
- See and navigate their knowledge as a storyboard
- Track and operate across multiple software projects
- Route work to multiple agents and control execution from one place

## 3. Target User
- Primary user: single individual (solo operator)
- User profile: builder/founder/developer managing many open loops across ideas, tasks, and repositories

## 4. Goals
1. Make WhatsApp the primary interaction channel for capture and commands.
2. Provide a storyboard view of the user's knowledge graph/second brain.
3. Provide cross-project code and GitHub status visibility in one dashboard.
4. Enable a unified multi-agent control plane for planning and execution.
5. Preserve trust with auditability, correction workflows, and explicit control over automation.

## 5. Non-Goals (MVP)
1. Team collaboration features (roles, permissions for multiple humans).
2. Full CI/CD replacement.
3. Autonomous production deploys without user approval.
4. Building custom foundation models.

## 6. Core User Jobs To Be Done
1. "Capture an idea/task instantly from WhatsApp without context switching."
2. "Open one board and understand what is in my brain and what needs action."
3. "See the health and status of all my GitHub projects in one place."
4. "Send work to the best agent and track status/results centrally."
5. "Correct bad AI routing/classification quickly and improve future behavior."

## 7. Functional Requirements
### 7.1 WhatsApp Interface
- `FR-1` Inbound capture via WhatsApp message (text, link, voice note transcript, image note metadata).
- `FR-2` WhatsApp command syntax for quick operations (examples: `add`, `status`, `today`, `run-agent`, `fix`).
- `FR-3` Outbound daily/periodic nudges delivered on WhatsApp.
- `FR-4` Critical alerts on failed automations or blocked agent runs.

### 7.2 Second Brain Pipeline
- `FR-5` Frictionless inbox/drop box for all incoming items.
- `FR-6` AI classification/routing with confidence score.
- `FR-7` Structured record creation (schema fields for type, project, priority, next action, source, timestamps).
- `FR-8` Memory store with searchable history and retrieval.
- `FR-9` Audit trail (receipt) for each automated decision/action.
- `FR-10` Confidence gate: low-confidence items go to review queue.
- `FR-11` Correction path: user can edit classification/fields and re-run processing.

### 7.3 Storyboard / Brain View
- `FR-12` Visual storyboard workspace for ideas, tasks, projects, and relationships.
- `FR-13` Multiple views: timeline, board, and graph/relationship view.
- `FR-14` Click-through from storyboard card/node to raw source, AI summary, and related actions.
- `FR-15` "What changed" view for last 24h/7d.

### 7.4 Codebase Workspace
- `FR-16` Register multiple local/remote codebases.
- `FR-17` Per-project snapshot: current branch, recent commits, open PRs, failing checks, pending TODOs/issues.
- `FR-18` Natural-language ask over code context (per selected project) and return actionable output.
- `FR-19` Task-to-code linking from storyboard items to repo/branch/PR.

### 7.5 GitHub Multi-Project Status
- `FR-20` Unified GitHub portfolio dashboard across selected repos.
- `FR-21` Status cards for PRs, Issues, CI checks, release cadence, stale branches.
- `FR-22` Daily digest summary of repo health and required interventions.
- `FR-23` Filter by project, priority, due risk, and blocked state.

### 7.6 Multi-Agent Orchestration
- `FR-24` Agent registry for Claude, Antigravity, Codex, and extensible future agents.
- `FR-25` Task router to choose agent by task type, context, and policy.
- `FR-26` Run control: start, pause, stop, retry, escalate-to-human.
- `FR-27` Shared run log with prompts, tool actions, outputs, and status.
- `FR-28` Ability to invoke agent actions from WhatsApp and receive result summaries in WhatsApp.

### 7.7 Control and Safety
- `FR-29` Explicit approval step for sensitive actions (merge, deploy, destructive git ops).
- `FR-30` Policy layer for allowed tools/actions per agent.
- `FR-31` Idempotent retries and restart-safe workflows.

## 8. Non-Functional Requirements
1. Reliability: no silent failures; failed jobs must be visible with reason and retry path.
2. Latency: standard capture-to-structured-item under 2 minutes.
3. Traceability: every automated action must have user-visible audit logs.
4. Privacy: user-scoped data isolation and secure token/key handling.
5. Maintainability: modular connectors (WhatsApp, GitHub, agents, memory store).
6. Extensibility: add new agents/integrations without core rewrite.

## 9. Data Model (MVP entities)
1. `InboxItem`
2. `BrainNode`
3. `Project`
4. `RepoStatus`
5. `Agent`
6. `AgentRun`
7. `PolicyRule`
8. `AuditEvent`
9. `Nudge`

## 10. Success Metrics (MVP)
1. Capture completion rate from WhatsApp (messages converted to structured items).
2. % of items auto-routed without manual correction.
3. Time-to-next-action from capture.
4. Daily active use of storyboard view.
5. Time saved on GitHub status checks across repos.
6. Agent task success rate and human override frequency.

## 11. MVP Milestones
1. Milestone A: WhatsApp capture + second-brain core loop + audit.
2. Milestone B: Storyboard brain view + corrections + nudges.
3. Milestone C: Multi-repo GitHub dashboard + codebase workspace.
4. Milestone D: Multi-agent orchestration + WhatsApp control commands.

## 12. Open Decisions
1. Preferred WhatsApp integration path (Cloud API provider and phone setup).
2. Primary memory store choice (Notion-first vs database-first with Notion sync).
3. Preferred auth model for GitHub and agent providers.
4. Depth of codebase operations allowed in MVP (read-only vs controlled write actions).
