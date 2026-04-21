import { describe, it } from "node:test";
import * as assert from "node:assert/strict";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { mkdtempSync, unlinkSync, existsSync } from "node:fs";
import { Chain } from "../src/chain.js";
import { verifyEntries, verifyFile } from "../src/verify.js";
import {
  sha256,
  computeDataHash,
  computeEntryHash,
  generateEd25519KeyPair,
  signEntry,
  verifySignature,
} from "../src/crypto.js";
import { makeEntry, entryToJsonLine, parseEntryLine } from "../src/entry.js";
import { buildRfc3161Tsq, parseTsrStatus } from "../src/anchor.js";

const tmpDir = mkdtempSync(join(tmpdir(), "coc-test-"));

describe("crypto", () => {
  it("sha256 produces correct hex digest", () => {
    const hash = sha256("hello");
    assert.equal(hash.length, 64);
    assert.equal(
      hash,
      "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824",
    );
  });

  it("computeDataHash matches sha256 of string", () => {
    const data = "test data";
    assert.equal(computeDataHash(data), sha256(data));
  });

  it("computeEntryHash uses pipe-delimited canonical form", () => {
    const hash = computeEntryHash(0, "2026-01-01T00:00:00Z", "genesis", "alex", "abc", "def");
    const expected = sha256("0|2026-01-01T00:00:00Z|genesis|alex|abc|def");
    assert.equal(hash, expected);
  });

  it("Ed25519 key generation, signing, and verification", () => {
    const { publicKey, privateKey } = generateEd25519KeyPair();
    const msg = "test_entry_hash_abc123";
    const sig = signEntry(msg, privateKey);
    assert.ok(sig.length > 0);
    assert.ok(verifySignature(msg, sig, publicKey));
    assert.ok(!verifySignature("wrong_message", sig, publicKey));
  });
});

describe("entry", () => {
  it("makeEntry creates valid entry with correct fields", () => {
    const entry = makeEntry({
      sequence: 0,
      eventType: "genesis",
      data: "Genesis block",
      prevHash: "0".repeat(64),
      agent: "test",
    });

    assert.equal(entry.seq, 0);
    assert.equal(entry.type, "genesis");
    assert.equal(entry.agent, "test");
    assert.equal(entry.prev_hash, "0".repeat(64));
    assert.equal(entry.data_hash, sha256("Genesis block"));
    assert.equal(entry.schema_version, "1.1");
    assert.ok(entry.ts);
    assert.ok(entry.entry_hash);
  });

  it("makeEntry with Ed25519 signing", () => {
    const { publicKey, privateKey } = generateEd25519KeyPair();
    const entry = makeEntry({
      sequence: 0,
      eventType: "genesis",
      data: "Signed genesis",
      prevHash: "0".repeat(64),
      privateKey,
    });

    assert.ok(entry.signature);
    assert.ok(verifySignature(entry.entry_hash, entry.signature!, publicKey));
  });

  it("entryToJsonLine and parseEntryLine roundtrip", () => {
    const entry = makeEntry({
      sequence: 1,
      eventType: "boot",
      data: "Boot event",
      prevHash: "a".repeat(64),
    });

    const line = entryToJsonLine(entry);
    const parsed = parseEntryLine(line);
    assert.deepEqual(parsed, entry);
  });
});

describe("Chain", () => {
  it("creates genesis on init", () => {
    const chain = new Chain({ agent: "test-agent" });
    assert.equal(chain.length, 1);
    assert.equal(chain.entries[0].type, "genesis");
    assert.equal(chain.entries[0].seq, 0);
    assert.equal(chain.agent, "test-agent");
  });

  it("adds entries with correct linkage", () => {
    const chain = new Chain({ agent: "test" });
    chain.add("boot", "Session started");
    chain.add("learn", "Learned something");

    assert.equal(chain.length, 3);
    assert.equal(chain.entries[1].type, "boot");
    assert.equal(chain.entries[2].type, "learn");
    assert.equal(chain.entries[1].prev_hash, chain.entries[0].entry_hash);
    assert.equal(chain.entries[2].prev_hash, chain.entries[1].entry_hash);
  });

  it("verifies a valid chain", () => {
    const chain = new Chain({ agent: "test" });
    chain.add("boot", "Started");
    chain.add("learn", "Knowledge acquired");
    chain.add("decide", "Made a decision");

    const result = chain.verify();
    assert.ok(result.valid);
    assert.equal(result.entries, 4);
    assert.equal(result.errors.length, 0);
    assert.deepEqual(result.types, {
      genesis: 1,
      boot: 1,
      learn: 1,
      decide: 1,
    });
  });

  it("detects tampered data", () => {
    const chain = new Chain({ agent: "test" });
    chain.add("boot", "Started");

    const entries = chain.exportJson();
    entries[1].data = "TAMPERED";

    const result = verifyEntries(entries);
    assert.ok(!result.valid);
    assert.ok(result.errors[0].includes("data_hash mismatch"));
  });

  it("detects broken linkage", () => {
    const chain = new Chain({ agent: "test" });
    chain.add("boot", "Started");
    chain.add("learn", "Knowledge");

    const entries = chain.exportJson();
    entries[2].prev_hash = "f".repeat(64);

    const result = verifyEntries(entries);
    assert.ok(!result.valid);
    assert.ok(result.errors[0].includes("prev_hash"));
  });

  it("handles session continuity (commitment + verification)", () => {
    const chain = new Chain({ agent: "test" });
    const commitHash = sha256("expected_state");
    chain.add("session_end", "Session ending", { commitment: commitHash });
    chain.add("session_start", "Session starting", { verification: commitHash });

    const result = chain.verify();
    assert.ok(result.valid);
    assert.equal(result.session_bridges, 1);
    assert.equal(result.session_mismatches, 0);

    const lastStart = chain.entries[chain.length - 1];
    assert.equal(lastStart.commitment_match, true);
  });

  it("detects session commitment mismatch", () => {
    const chain = new Chain({ agent: "test" });
    chain.add("session_end", "Ending", { commitment: sha256("expected") });
    chain.add("session_start", "Starting", { verification: sha256("different") });

    const result = chain.verify();
    assert.ok(result.valid);
    assert.equal(result.session_bridges, 1);
    assert.equal(result.session_mismatches, 1);
  });

  it("persists to and loads from file", () => {
    const filePath = join(tmpDir, "test-chain.jsonl");
    const chain1 = new Chain({ agent: "file-test", storage: filePath });
    chain1.add("boot", "Started");
    chain1.add("learn", "Knowledge");

    const chain2 = Chain.fromFile(filePath, { agent: "file-test" });
    assert.equal(chain2.length, 3);
    assert.equal(chain2.entries[0].type, "genesis");
    assert.equal(chain2.entries[1].type, "boot");

    const result = chain2.verify();
    assert.ok(result.valid);
  });

  it("exports to JSON array", () => {
    const chain = new Chain({ agent: "export-test" });
    chain.add("milestone", "First milestone");

    const exportPath = join(tmpDir, "export.json");
    chain.export(exportPath, { pretty: true });

    const result = verifyFile(exportPath);
    assert.ok(result.valid);
    assert.equal(result.entries, 2);
  });

  it("fromJson reconstructs chain", () => {
    const chain1 = new Chain({ agent: "json-test" });
    chain1.add("boot", "Started");
    const json = chain1.exportJson();

    const chain2 = Chain.fromJson(json, { agent: "json-test" });
    assert.equal(chain2.length, 2);
    assert.ok(chain2.verify().valid);
  });

  it("handles object data (JSON-serialized)", () => {
    const chain = new Chain({ agent: "test" });
    chain.add("learn", { topic: "cryptography", score: 95 });

    assert.equal(chain.length, 2);
    const entry = chain.entries[1];
    const parsed = JSON.parse(entry.data);
    assert.equal(parsed.topic, "cryptography");
  });

  it("latest and head return correct values", () => {
    const chain = new Chain({ agent: "test" });
    chain.add("boot", "Started");

    assert.ok(chain.latest);
    assert.equal(chain.latest!.type, "boot");
    assert.equal(chain.head, chain.latest!.entry_hash);
  });

  it("toString returns informative string", () => {
    const chain = new Chain({ agent: "test" });
    assert.ok(chain.toString().includes("test"));
    assert.ok(chain.toString().includes("1"));
  });

  it("verifies chain with Ed25519 signatures", () => {
    const { publicKey, privateKey } = generateEd25519KeyPair();
    const chain = new Chain({ agent: "signed", privateKey });
    chain.add("boot", "Signed boot");
    chain.add("learn", "Signed learning");

    const result = chain.verify(publicKey);
    assert.ok(result.valid);
    assert.equal(result.entries, 3);
  });
});

describe("input validation", () => {
  it("rejects invalid event type", () => {
    const chain = new Chain({ agent: "test" });
    assert.throws(() => chain.add("invalid_type", "data"), /Invalid event type/);
  });

  it("rejects malformed commitment hash", () => {
    const chain = new Chain({ agent: "test" });
    assert.throws(
      () => chain.add("session_end", "Ending", { commitment: "not-a-hash" }),
      /commitment must be/,
    );
  });

  it("rejects malformed verification hash", () => {
    const chain = new Chain({ agent: "test" });
    assert.throws(
      () => chain.add("session_start", "Starting", { verification: "bad" }),
      /verification must be/,
    );
  });
});

describe("anchor", () => {
  it("buildRfc3161Tsq produces valid DER", () => {
    const hashBytes = Buffer.alloc(32, 0xab);
    const tsq = buildRfc3161Tsq(hashBytes);
    assert.ok(tsq.length > 0);
    assert.equal(tsq[0], 0x30);
  });

  it("parseTsrStatus handles malformed input", () => {
    const result = parseTsrStatus(Buffer.from([0x00, 0x01]));
    assert.equal(result.status, -1);
    assert.ok(result.status_text.includes("parse_error"));
  });
});

describe("verifyEntries edge cases", () => {
  it("empty chain", () => {
    const result = verifyEntries([]);
    assert.ok(!result.valid);
    assert.ok(result.errors[0].includes("empty"));
  });

  it("non-genesis first entry", () => {
    const entry = makeEntry({
      sequence: 0,
      eventType: "boot",
      data: "Not genesis",
      prevHash: "0".repeat(64),
    });
    const result = verifyEntries([entry]);
    assert.ok(!result.valid);
    assert.ok(result.errors[0].includes("not genesis"));
  });

  it("wrong genesis prev_hash", () => {
    const entry = makeEntry({
      sequence: 0,
      eventType: "genesis",
      data: "Bad genesis",
      prevHash: "a".repeat(64),
    });
    const result = verifyEntries([entry]);
    assert.ok(!result.valid);
    assert.ok(result.errors[0].includes("prev_hash"));
  });

  it("sequence mismatch", () => {
    const genesis = makeEntry({
      sequence: 0,
      eventType: "genesis",
      data: "Genesis",
      prevHash: "0".repeat(64),
    });
    const bad = makeEntry({
      sequence: 5,
      eventType: "boot",
      data: "Wrong seq",
      prevHash: genesis.entry_hash,
    });
    const result = verifyEntries([genesis, bad]);
    assert.ok(!result.valid);
    assert.ok(result.errors[0].includes("sequence mismatch"));
  });
});
