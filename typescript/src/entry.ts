import type { KeyObject } from "node:crypto";
import { computeDataHash, computeEntryHash, signEntry } from "./crypto.js";
import type { ChainEntryData } from "./types.js";
import { SCHEMA_VERSION } from "./types.js";

export function makeEntry(opts: {
  sequence: number;
  eventType: string;
  data: string;
  prevHash: string;
  agent?: string;
  commitment?: string | null;
  verification?: string | null;
  commitmentMatch?: boolean | null;
  privateKey?: KeyObject;
}): ChainEntryData {
  const {
    sequence,
    eventType,
    data,
    prevHash,
    agent = "agent",
    commitment = null,
    verification = null,
    commitmentMatch = null,
    privateKey,
  } = opts;

  const ts = new Date().toISOString();
  const dataHash = computeDataHash(data);
  const entryHash = computeEntryHash(
    sequence,
    ts,
    eventType,
    agent,
    dataHash,
    prevHash,
  );

  const entry: ChainEntryData = {
    seq: sequence,
    ts,
    type: eventType,
    agent,
    data,
    data_hash: dataHash,
    prev_hash: prevHash,
    entry_hash: entryHash,
    schema_version: SCHEMA_VERSION,
  };

  if (commitment !== null) entry.commitment = commitment;
  if (verification !== null) entry.verification = verification;
  if (commitmentMatch !== null) entry.commitment_match = commitmentMatch;

  if (privateKey) {
    entry.signature = signEntry(entryHash, privateKey);
  }

  return entry;
}

export function entryToJsonLine(entry: ChainEntryData): string {
  return JSON.stringify(entry);
}

export function parseEntryLine(line: string): ChainEntryData {
  return JSON.parse(line) as ChainEntryData;
}
