# Product Improvement Campaign

This repo runs a compounding product-improvement campaign for the whole brain system, not only the public website.

## Scope

Every cycle can touch:

- public website and case studies
- Open Brain chat
- Discord tools and review flows
- daily dumps and board outputs
- retrieval and memory quality
- planner, reminders, and autonomous routines
- MCP and coding-agent ergonomics

## Cycle Stages

Each product cycle follows the same six stages:

1. PM pass
   Detect gaps, stale flows, weak UX, missing features, and obvious product opportunities.
2. Plan pass
   Pick the highest-signal opportunity and define the implementation focus for the cycle.
3. Engineering pass
   Apply code and configuration changes for that cycle.
4. QA pass
   Run 10 QA lenses:
   data-contract, parser/curation, route/API, case-study/content, architecture-diagram, responsive/layout, design/polish, Open Brain chat, Discord workflow, deploy/rollback smoke.
5. UAT pass
   Run 3 UAT lenses:
   recruiter view, collaborator/client view, Ahmad-owner/taste view.
6. Closeout
   Publish a report with what improved, why it mattered, what happened in the stage breakdown, and what should happen next.

## Approval Model

- The campaign runs in waves of 5 cycles.
- Cycles 1-4 within a wave can continue automatically if their QA/UAT gates stay green.
- Cycle 5 closes the wave and generates a review package.
- Approval is required after cycles 5, 10, 15, and 20 before the next wave can begin.
- Approval can happen from the dashboard API or from the Discord review thread.

## Reporting

Every cycle must leave behind a structured report that includes:

- overview
- improvements made
- why each improvement mattered
- QA summary
- UAT summary
- stage-by-stage status
- whether approval is required before the next cycle

## Wave Exit

At the end of each 5-cycle wave:

- publish a wave report to Discord
- create a review item in `#needs-review`
- pause the campaign until approval is recorded

After 20 cycles:

- run a full-system regression
- close remaining P1/P2 issues
- move to steady-state daily improvement mode
