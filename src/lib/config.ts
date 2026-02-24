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

  // NATS subjects
  subjects: {
    inboxRaw: 'inbox.raw',
    inboxClassified: 'inbox.classified',
    inboxReview: 'inbox.review',
    whatsappOutbound: 'whatsapp.outbound',
  },
} as const;
