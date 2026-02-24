import pg from 'pg';
import { config } from './config.js';

const pool = new pg.Pool({
  connectionString: config.database.connectionString,
  max: 10,
  idleTimeoutMillis: 30000,
});

pool.on('error', (err) => {
  console.error('[DB] Unexpected pool error:', err.message);
});

export async function query<T extends pg.QueryResultRow = any>(
  text: string,
  params?: any[],
): Promise<pg.QueryResult<T>> {
  const start = Date.now();
  const result = await pool.query<T>(text, params);
  const duration = Date.now() - start;
  if (duration > 500) {
    console.warn(`[DB] Slow query (${duration}ms): ${text.substring(0, 80)}...`);
  }
  return result;
}

export async function getClient(): Promise<pg.PoolClient> {
  return pool.connect();
}

export async function shutdown(): Promise<void> {
  await pool.end();
  console.log('[DB] Pool closed');
}

export { pool };
