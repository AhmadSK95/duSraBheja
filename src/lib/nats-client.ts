import { connect, NatsConnection, JetStreamManager, JetStreamClient, StringCodec } from 'nats';
import { config } from './config.js';

let nc: NatsConnection | null = null;
let js: JetStreamClient | null = null;

const sc = StringCodec();

export async function getNatsConnection(): Promise<NatsConnection> {
  if (!nc || nc.isClosed()) {
    nc = await connect({ servers: config.nats.url });
    console.log(`[NATS] Connected to ${config.nats.url}`);
  }
  return nc;
}

export async function getJetStream(): Promise<JetStreamClient> {
  if (!js) {
    const conn = await getNatsConnection();
    js = conn.jetstream();
  }
  return js;
}

export async function ensureStream(name: string, subjects: string[]): Promise<void> {
  const conn = await getNatsConnection();
  const jsm: JetStreamManager = await conn.jetstreamManager();
  try {
    await jsm.streams.info(name);
    console.log(`[NATS] Stream '${name}' exists`);
  } catch {
    await jsm.streams.add({
      name,
      subjects,
      retention: 'limits' as any,
      max_msgs: 100000,
      max_bytes: 500 * 1024 * 1024, // 500MB
      max_age: 30 * 24 * 60 * 60 * 1_000_000_000, // 30 days in nanos
    });
    console.log(`[NATS] Stream '${name}' created with subjects: ${subjects.join(', ')}`);
  }
}

export function encode(data: string): Uint8Array {
  return sc.encode(data);
}

export function decode(data: Uint8Array): string {
  return sc.decode(data);
}

export async function publish(subject: string, data: object | string): Promise<void> {
  const conn = await getNatsConnection();
  const payload = typeof data === 'string' ? data : JSON.stringify(data);
  conn.publish(subject, sc.encode(payload));
}

export async function shutdown(): Promise<void> {
  if (nc) {
    await nc.drain();
    console.log('[NATS] Connection closed');
  }
}
