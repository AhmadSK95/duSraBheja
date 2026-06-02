---
# Owner-curated narrative for buildwithmoenu.com.
# Read at snapshot rebuild time by build_profile_narrative() in
# src/services/public_surface.py. Edit here; the public site picks it up
# on the next refresh (manual: scripts/refresh_public_surface.py, or daily cron).
name: Moenuddeen Ahmad Shaik
preferred_name: Ahmad
location: Jersey City, NJ
hero_title: "Builder, post-Amazon. Two live client sites, two AI products in flight."
hero_subtitle: "IIT Kharagpur → NYU Tandon → Amazon Ads (3y 4m) → builder phase. Jersey City. Oscar the cat."
identity: "I start from problems, not tech stacks. I don't just write code — I ship products."
professional_summary: |
  Builder. Three years and four months at Amazon Ads building international ad delivery in Java/Python, then walked away in September 2025 to build product instead of complexity. Two live freelance sites in Jersey City and two AI products in flight. Applying selectively to AI/ML roles where the mission matches.
hero_bullets:
  - "IIT Kharagpur to New York, with Amazon-scale systems and enterprise backend work in between."
  - "Current phase is less about titles and more about ownership, product taste, and shipping tools worth carrying."
  - "The person in the system matters too: Oscar, Jersey City, anime, music, a bias toward things that feel alive."
current_arc:
  throughline: "Start from problems, not tech stacks. Don't just write code — ship products."
  acts:
    - period: "India · 2013–2021"
      label: "Foundation"
      body: "IIT Kharagpur Electrical Engineering, KVPY scholar. Then Loylty Rewardz in Mumbai, leading a batch→Kafka migration on a loyalty engine processing millions of transactions a day. First real production chops."
    - period: "NYC · 2021–2025"
      label: "Enterprise"
      body: "NYU Tandon MS. Then Amazon Ads — Java/Python microservices powering international marketplace expansion, multi-region AWS at 99.9%+ availability. Learned distributed-systems discipline. Learned advertising wasn't the product I wanted to build."
    - period: "Now · Sep 2025 → present"
      label: "Builder"
      body: "Walked away from comp. Two live freelance sites. Two AI products. Selective with what's next."
  focus:
    - "Shipping smaller, deploying earlier, letting real usage drive the next agent rather than designing five up front."
    - "Owner-grade ops on small client sites — release versioning, atomic cutover, zero-downtime cert renewal."
    - "Agentic systems where orchestration matters more than the model — retrieval, error recovery, knowing when to stop decomposing."
education:
  - period: "2021–2022"
    school: "NYU Tandon School of Engineering"
    degree: "MS, Electrical Engineering"
    details: "ML, Deep Learning, Big Data coursework."
  - period: "2013–2017"
    school: "IIT Kharagpur"
    degree: "BTech, Electrical Engineering"
    details: "Computer Vision, IoT projects. KVPY scholar."
roles:
  - period: "2021–2025"
    organization: "Amazon"
    title: "Software Development Engineer · Advertising"
    location: "NYC"
    summary: "Built Java/Python microservices powering international marketplace expansion across multi-region AWS at 99.9%+ availability."
    bullets:
      - "Ad delivery services that scaled to international markets without sacrificing latency budgets."
      - "Distributed-systems discipline at Amazon scale — caches, queues, retries, observability."
      - "Left when the technical complexity stopped translating into product I cared about."
  - period: "2018–2021"
    organization: "Loylty Rewardz"
    title: "Software Engineer"
    location: "Mumbai"
    summary: "Led a batch→Kafka migration on a loyalty engine processing millions of transactions a day."
    bullets:
      - "Apache Camel + Kafka pipelines replacing fragile batch jobs."
      - "First real ownership over production systems people actually depended on."
skills:
  - category: "Languages"
    items: ["Java", "Python", "JavaScript", "TypeScript", "SQL"]
  - category: "Backend"
    items: ["FastAPI", "Node / Express", "Spring", "Apache Kafka", "Apache Camel"]
  - category: "Frontend"
    items: ["React", "Next.js", "vanilla JS when it's the right call"]
  - category: "AI / ML"
    items: ["LangChain patterns", "ReAct loops", "MCP", "Multi-provider routing", "Sentence-transformers", "pgvector retrieval"]
  - category: "Data"
    items: ["PostgreSQL", "pgvector", "DuckDB", "Redis", "Parquet"]
  - category: "Ops"
    items: ["AWS (Amazon scale)", "DigitalOcean", "Docker", "Nginx", "Let's Encrypt", "Deploy pipelines you can hand off"]
capabilities:
  - title: "Distributed backend"
  - title: "AI agents & MCP"
  - title: "Retrieval-grounded search"
  - title: "Product-grade client websites"
  - title: "Self-hosted ops"
personal_signals:
  cat:
    name: "Oscar"
    body: "Orange tabby. White chest, white paws, green collar. Predates the US move. Shows up in half my Instagram."
  city:
    label: "Jersey City"
    body: "Cycle around. North Face puffer. Beanie. Octagonal glasses."
  listening:
    label: "Listening"
    body: "Seedhe Maut, Raftaar, Kendrick (GNX), Yashraj, MC SQUARE."
  watching:
    label: "Watching"
    body: "Naruto Shippuden S9. Brooklyn Nine-Nine binge. Rewatch comfort is Person of Interest."
  cooking:
    label: "Cooking"
    body: "Chicken stroganoff, hamantaschen, anything that involves an oven and a quiet hour."
contact_open_to:
  - "Freelance product builds with real ownership — small scope, end-to-end."
  - "Remote roles at startups or companies building products with a real vision — solving an actual problem in the real world."
  - "Conversations about agentic systems, MCP, retrieval, multi-provider routing — especially with people building product-first."
contact_lede: "Email is fastest. LinkedIn is fine. Instagram is for Oscar."
open_brain_topics:
  - title: "Engineering fit"
    summary: "Distributed backend, AI orchestration, Amazon-scale ops — what kind of role lines up with what I want to build."
  - title: "The four projects"
    summary: "Tradeoffs, decisions, what's live versus what's still in-flight."
  - title: "Builder phase"
    summary: "Why I left Amazon, what's worth shipping now, and where my time goes."
  - title: "Agentic systems"
    summary: "MCP, retrieval, multi-provider routing, when to stop decomposing."
---

# Editing notes

This file is the canonical source for owner-authored copy on
buildwithmoenu.com. The YAML frontmatter above is parsed by
`build_profile_narrative()` in `src/services/public_surface.py` and assembled
into the public snapshot payload at refresh time.

Anything below this line is not parsed — it's for the editor's notes.

## What lives where

- **Hero copy, arc, education, skills, roles** → fields above, parsed into
  the profile snapshot payload.
- **Photos** → see `public-seed/photo_map.json`.
- **Project case studies** → one JSON file per project under
  `public-seed/case_studies/`.
- **Project allowlist (which slugs surface)** → `PUBLIC_PROJECT_ALLOWLIST`
  env var on the droplet.
- **Contact links** → `PUBLIC_CONTACT_*` env vars on the droplet.
- **Approved public facts (Discord-captured)** → DB, gated by the dashboard
  approval queue at `/dashboard/public-facts`.

## Private — do not surface

- Phone number, home address, apartment details
- Partner's name, partner photos, wedding photos
- Internal infra (droplet IP, ports, repo URLs, file paths)
- Job-hunt internals (company shortlist, comp tiers, application statuses)
- GPA numbers, KVPY rank
- Employer-internal lore (manager names, team names)
