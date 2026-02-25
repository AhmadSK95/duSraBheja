import 'dotenv/config';

export const config = {
  // PostgreSQL
  database: {
    connectionString: process.env.DATABASE_URL || 'postgresql://localhost:5432/dusrabheja',
  },

  // NATS
  nats: {
    url: process.env.NATS_URL || 'nats://127.0.0.1:4222',
  },

  // Ollama
  ollama: {
    host: process.env.OLLAMA_HOST || 'http://localhost:11434',
    classifyModel: process.env.OLLAMA_CLASSIFY_MODEL || 'llama3.1:8b',
    embedModel: process.env.OLLAMA_EMBED_MODEL || 'nomic-embed-text',
    summaryModel: process.env.OLLAMA_SUMMARY_MODEL || 'llama3.1:8b',
  },

  // Temporal
  temporal: {
    address: process.env.TEMPORAL_ADDRESS || 'localhost:7233',
    namespace: process.env.TEMPORAL_NAMESPACE || 'default',
    taskQueue: 'dusrabheja-main',
  },

  // Classification
  confidenceThreshold: parseFloat(process.env.CONFIDENCE_THRESHOLD || '0.7'),

  // Anthropic (Claude Sonnet)
  anthropic: {
    apiKey: process.env.ANTHROPIC_API_KEY || '',
    plannerModel: process.env.ANTHROPIC_PLANNER_MODEL || 'claude-sonnet-4-20250514',
    executorModel: process.env.ANTHROPIC_EXECUTOR_MODEL || 'claude-sonnet-4-20250514',
  },

  // Google (Gemini)
  gemini: {
    apiKey: process.env.GOOGLE_API_KEY || '',
    criticModel: process.env.GEMINI_CRITIC_MODEL || 'gemini-2.5-pro',
  },

  // Agent subsystem
  agents: {
    approvalTimeoutMinutes: parseInt(process.env.APPROVAL_TIMEOUT_MINUTES || '30', 10),
    maxConcurrentRuns: parseInt(process.env.MAX_CONCURRENT_RUNS || '3', 10),
  },

  // GitHub
  github: {
    token: process.env.GITHUB_TOKEN || '',
    apiBase: process.env.GITHUB_API_BASE || 'https://api.github.com',
    pollIntervalMinutes: parseInt(process.env.GITHUB_POLL_INTERVAL_MINUTES || '15', 10),
  },

  // Nudge
  nudge: {
    staleDaysThreshold: parseInt(process.env.STALE_DAYS_THRESHOLD || '3', 10),
    checkIntervalHours: parseInt(process.env.NUDGE_CHECK_INTERVAL_HOURS || '6', 10),
  },

  // NATS subjects
  subjects: {
    inboxRaw: 'inbox.raw',
    inboxClassified: 'inbox.classified',
    inboxReview: 'inbox.review',
    whatsappOutbound: 'whatsapp.outbound',
    githubSync: 'github.sync',
    githubAlert: 'github.alert',
    nudgeSend: 'nudge.send',
    systemKill: 'system.kill',
    systemResume: 'system.resume',
    agentRun: 'agent.run',
    agentComplete: 'agent.complete',
  },
} as const;
