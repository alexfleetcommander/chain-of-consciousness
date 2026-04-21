import type { KeyObject } from "node:crypto";

export const SCHEMA_VERSION = "1.1";

export const VALID_EVENT_TYPES = [
  "genesis",
  "boot",
  "learn",
  "decide",
  "create",
  "milestone",
  "rotate",
  "anchor",
  "error",
  "note",
  "session_start",
  "session_end",
  "compaction",
  "governance",
] as const;

export type EventType = (typeof VALID_EVENT_TYPES)[number];

export interface ChainEntryData {
  seq: number;
  ts: string;
  type: string;
  agent: string;
  data: string;
  data_hash: string;
  prev_hash: string;
  entry_hash: string;
  schema_version: string;
  commitment?: string | null;
  verification?: string | null;
  commitment_match?: boolean | null;
  signature?: string | null;
}

export interface VerifyResult {
  valid: boolean;
  entries: number;
  errors: string[];
  genesis_ts: string | null;
  latest_ts: string | null;
  agents: Record<string, number>;
  types: Record<string, number>;
  anchors: string[];
  session_bridges: number;
  session_mismatches: number;
  schema_versions: Record<string, { first: number; last: number }>;
}

export interface AnchorResult {
  success: boolean;
  anchor_type: string;
  proof?: Buffer;
  error?: string;
  timestamp?: string;
}

export interface TsaStatus {
  status: number;
  status_text: string;
  has_token: boolean;
  tsr_size: number;
}

export interface ChainOptions {
  agent?: string;
  storage?: string;
  privateKey?: KeyObject;
}

export interface ExportOptions {
  pretty?: boolean;
}
