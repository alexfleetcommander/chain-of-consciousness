import {
  existsSync,
  readFileSync,
  writeFileSync,
  appendFileSync,
  mkdirSync,
} from "node:fs";
import { dirname } from "node:path";
import type { KeyObject } from "node:crypto";
import { makeEntry, entryToJsonLine, parseEntryLine } from "./entry.js";
import { verifyEntries } from "./verify.js";
import type {
  ChainEntryData,
  ChainOptions,
  ExportOptions,
  VerifyResult,
} from "./types.js";
import { VALID_EVENT_TYPES } from "./types.js";

export class Chain {
  readonly agent: string;
  readonly storage: string | null;
  private _entries: ChainEntryData[] = [];
  private _privateKey: KeyObject | undefined;

  constructor(opts: ChainOptions = {}) {
    this.agent = opts.agent ?? "agent";
    this.storage = opts.storage ?? null;
    this._privateKey = opts.privateKey;

    if (this.storage && existsSync(this.storage)) {
      this._load();
    } else {
      this._initGenesis();
    }
  }

  private _initGenesis(): void {
    const now = new Date()
      .toISOString()
      .replace("T", " ")
      .replace(/\.\d+Z$/, " UTC");
    const genesis = makeEntry({
      sequence: 0,
      eventType: "genesis",
      data: `Genesis block. Agent: ${this.agent}. Initialized: ${now}.`,
      prevHash: "0".repeat(64),
      agent: this.agent,
      privateKey: this._privateKey,
    });
    this._entries = [genesis];
    if (this.storage) {
      this._flush();
    }
  }

  private _load(): void {
    if (!this.storage) return;
    const content = readFileSync(this.storage, "utf-8");
    this._entries = content
      .split("\n")
      .filter((l) => l.trim())
      .map((l) => parseEntryLine(l));
  }

  private _flush(): void {
    if (!this.storage) return;
    const dir = dirname(this.storage);
    if (dir) mkdirSync(dir, { recursive: true });
    const lines = this._entries.map((e) => entryToJsonLine(e)).join("\n") + "\n";
    writeFileSync(this.storage, lines, "utf-8");
  }

  private _appendToFile(entry: ChainEntryData): void {
    if (!this.storage) return;
    const dir = dirname(this.storage);
    if (dir) mkdirSync(dir, { recursive: true });
    appendFileSync(this.storage, entryToJsonLine(entry) + "\n", "utf-8");
  }

  add(
    eventType: string,
    data: string | Record<string, unknown> = "",
    opts: {
      commitment?: string;
      verification?: string;
    } = {},
  ): ChainEntryData {
    const dataStr =
      typeof data === "string"
        ? data
        : JSON.stringify(data, Object.keys(data).sort());

    const eventLower = eventType.toLowerCase();
    if (!(VALID_EVENT_TYPES as readonly string[]).includes(eventLower)) {
      throw new Error(
        `Invalid event type '${eventLower}'. Valid: ${VALID_EVENT_TYPES.join(", ")}`,
      );
    }
    const prevHash =
      this._entries.length > 0
        ? this._entries[this._entries.length - 1].entry_hash
        : "0".repeat(64);
    const seq = this._entries.length;

    const HEX64 = /^[0-9a-f]{64}$/;
    if (opts.commitment && !HEX64.test(opts.commitment)) {
      throw new Error("commitment must be a 64-character lowercase hex SHA-256 hash");
    }
    if (opts.verification && !HEX64.test(opts.verification)) {
      throw new Error("verification must be a 64-character lowercase hex SHA-256 hash");
    }

    let commitmentMatch: boolean | null = null;
    if (eventLower === "session_start" && opts.verification) {
      const lastCommitment = this._findLastCommitment();
      if (lastCommitment) {
        commitmentMatch = opts.verification === lastCommitment;
      }
    }

    const entry = makeEntry({
      sequence: seq,
      eventType: eventLower,
      data: dataStr,
      prevHash,
      agent: this.agent,
      commitment: eventLower === "session_end" ? (opts.commitment ?? null) : null,
      verification:
        eventLower === "session_start" ? (opts.verification ?? null) : null,
      commitmentMatch,
      privateKey: this._privateKey,
    });

    this._entries.push(entry);
    this._appendToFile(entry);
    return entry;
  }

  private _findLastCommitment(): string | null {
    for (let i = this._entries.length - 1; i >= 0; i--) {
      const e = this._entries[i];
      if (e.type === "session_end" && e.commitment) {
        return e.commitment;
      }
    }
    return null;
  }

  verify(publicKey?: KeyObject): VerifyResult {
    return verifyEntries(this._entries, publicKey);
  }

  export(path: string, opts: ExportOptions = {}): void {
    const dir = dirname(path);
    if (dir) mkdirSync(dir, { recursive: true });
    const json = opts.pretty
      ? JSON.stringify(this._entries, null, 2)
      : JSON.stringify(this._entries);
    writeFileSync(path, json, "utf-8");
  }

  exportJson(): ChainEntryData[] {
    return this._entries.map((e) => ({ ...e }));
  }

  static fromFile(path: string, opts: Omit<ChainOptions, "storage"> = {}): Chain {
    return new Chain({ ...opts, storage: path });
  }

  static fromJson(
    entries: ChainEntryData[],
    opts: Omit<ChainOptions, "storage"> = {},
  ): Chain {
    const chain = Object.create(Chain.prototype) as Chain;
    Object.defineProperty(chain, "agent", { value: opts.agent ?? "agent" });
    Object.defineProperty(chain, "storage", { value: null });
    chain._entries = entries.map((e) => ({ ...e }));
    chain._privateKey = opts.privateKey;
    return chain;
  }

  get entries(): readonly ChainEntryData[] {
    return this._entries;
  }

  get latest(): ChainEntryData | null {
    return this._entries.length > 0
      ? this._entries[this._entries.length - 1]
      : null;
  }

  get length(): number {
    return this._entries.length;
  }

  get head(): string | null {
    return this.latest?.entry_hash ?? null;
  }

  toString(): string {
    return `Chain(agent=${this.agent}, entries=${this._entries.length}, storage=${this.storage})`;
  }
}
