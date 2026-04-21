import { computeDataHash, computeEntryHash, verifySignature } from "./crypto.js";
import type { ChainEntryData, VerifyResult } from "./types.js";
import type { KeyObject } from "node:crypto";
import { readFileSync } from "node:fs";

const ZERO_HASH = "0".repeat(64);

export function verifyEntries(
  chain: ChainEntryData[],
  publicKey?: KeyObject,
): VerifyResult {
  const result: VerifyResult = {
    valid: false,
    entries: chain.length,
    errors: [],
    genesis_ts: null,
    latest_ts: null,
    agents: {},
    types: {},
    anchors: [],
    session_bridges: 0,
    session_mismatches: 0,
    schema_versions: {},
  };

  if (chain.length === 0) {
    result.errors.push("Chain is empty");
    return result;
  }

  if (chain[0].type !== "genesis") {
    result.errors.push(
      `Entry 0 is not genesis (type=${chain[0].type})`,
    );
    return result;
  }

  if (chain[0].prev_hash !== ZERO_HASH) {
    result.errors.push("Genesis prev_hash is not zeros");
    return result;
  }

  result.genesis_ts = chain[0].ts ?? null;

  for (let i = 0; i < chain.length; i++) {
    const entry = chain[i];

    if (entry.seq !== i) {
      result.errors.push(
        `Entry ${i}: sequence mismatch (expected ${i}, got ${entry.seq})`,
      );
      return result;
    }

    const expectedDataHash = computeDataHash(entry.data);
    if (entry.data_hash !== expectedDataHash) {
      result.errors.push(`Entry ${i}: data_hash mismatch`);
      return result;
    }

    if (i > 0 && entry.prev_hash !== chain[i - 1].entry_hash) {
      result.errors.push(
        `Entry ${i}: prev_hash doesn't match entry ${i - 1} hash`,
      );
      return result;
    }

    const expectedEntryHash = computeEntryHash(
      entry.seq,
      entry.ts,
      entry.type,
      entry.agent,
      entry.data_hash,
      entry.prev_hash,
    );
    if (entry.entry_hash !== expectedEntryHash) {
      result.errors.push(
        `Entry ${i}: entry_hash mismatch (computed from stored fields)`,
      );
      return result;
    }

    if (publicKey && entry.signature) {
      const sigValid = verifySignature(
        entry.entry_hash,
        entry.signature,
        publicKey,
      );
      if (!sigValid) {
        result.errors.push(`Entry ${i}: Ed25519 signature invalid`);
        return result;
      }
    }

    const etype = entry.type;
    result.types[etype] = (result.types[etype] ?? 0) + 1;
    const agent = entry.agent;
    result.agents[agent] = (result.agents[agent] ?? 0) + 1;

    if (etype === "anchor") {
      result.anchors.push(entry.ts);
    }

    const sv = entry.schema_version ?? "1.0";
    if (!result.schema_versions[sv]) {
      result.schema_versions[sv] = { first: i, last: i };
    } else {
      result.schema_versions[sv].last = i;
    }

    if (etype === "session_start" && entry.verification) {
      result.session_bridges++;
      if (entry.commitment_match === false) {
        result.session_mismatches++;
      }
    }
  }

  result.latest_ts = chain[chain.length - 1].ts ?? null;
  result.valid = true;
  return result;
}

export function verifyFile(
  path: string,
  publicKey?: KeyObject,
): VerifyResult {
  const content = readFileSync(path, "utf-8").trim();

  if (!content) {
    return {
      valid: false,
      entries: 0,
      errors: ["File is empty"],
      genesis_ts: null,
      latest_ts: null,
      agents: {},
      types: {},
      anchors: [],
      session_bridges: 0,
      session_mismatches: 0,
      schema_versions: {},
    };
  }

  let chain: ChainEntryData[];
  if (content.startsWith("[")) {
    chain = JSON.parse(content);
  } else {
    chain = content
      .split("\n")
      .filter((l) => l.trim())
      .map((l) => JSON.parse(l));
  }

  return verifyEntries(chain, publicKey);
}
