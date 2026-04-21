# Chain of Consciousness — TypeScript

Cryptographic provenance chain for AI agents. TypeScript reference implementation of the [Chain of Consciousness protocol](https://vibeagentmaking.com).

An append-only, SHA-256-linked hash chain that records agent lifecycle events (boot, learning, decisions, milestones), with optional Ed25519 signing and external timestamp anchoring via OpenTimestamps (Bitcoin) and RFC 3161 TSAs.

## Install

```bash
npm install chain-of-consciousness
```

Requires Node.js >= 18.0.0. Zero external dependencies — uses only Node.js built-in `crypto`, `fs`, and `https` modules.

## Quick Start

```typescript
import { Chain } from "chain-of-consciousness";

// Create a new chain (auto-creates genesis block)
const chain = new Chain({ agent: "my-agent" });

// Add events
chain.add("boot", "Agent started, cycle 1");
chain.add("learn", "Processed 50 knowledge files");
chain.add("decide", "Selected strategy A over strategy B");
chain.add("milestone", "Reached 1000 processed tickets");

// Verify chain integrity
const result = chain.verify();
console.log(result.valid);    // true
console.log(result.entries);  // 5
console.log(result.types);    // { genesis: 1, boot: 1, learn: 1, decide: 1, milestone: 1 }
```

## Persistent Storage

```typescript
// Chain persists to JSONL file
const chain = new Chain({ agent: "my-agent", storage: "./chain.jsonl" });
chain.add("boot", "Session started");

// Reload from file later
const loaded = Chain.fromFile("./chain.jsonl");
console.log(loaded.length); // 2
console.log(loaded.verify().valid); // true
```

## Ed25519 Signing

```typescript
import { Chain, generateEd25519KeyPair } from "chain-of-consciousness";

const { publicKey, privateKey } = generateEd25519KeyPair();

// Every entry gets a cryptographic signature
const chain = new Chain({ agent: "signed-agent", privateKey });
chain.add("boot", "Signed boot event");

// Verify signatures
const result = chain.verify(publicKey);
console.log(result.valid); // true
```

## Session Continuity

The protocol bridges discontinuous sessions with forward-commitment hashing:

```typescript
import { Chain, sha256 } from "chain-of-consciousness";

const chain = new Chain({ agent: "my-agent" });

// End session with a commitment to expected next-session state
const stateHash = sha256("serialized_agent_state");
chain.add("session_end", "Session 1 complete", { commitment: stateHash });

// Start next session — verify bootstrap matches commitment
chain.add("session_start", "Session 2 begin", { verification: stateHash });

const result = chain.verify();
console.log(result.session_bridges);    // 1
console.log(result.session_mismatches); // 0
```

## External Anchoring

### RFC 3161 Timestamp Authority

```typescript
import { submitTsa, computeChainHash } from "chain-of-consciousness";

const chainHash = computeChainHash("./chain.jsonl");
const result = await submitTsa(chainHash);
if (result.success) {
  // result.proof contains the DER-encoded timestamp response
  console.log("Anchored at:", result.timestamp);
}
```

### OpenTimestamps (Bitcoin)

```typescript
import { submitOts, computeChainHash } from "chain-of-consciousness";

const chainHash = computeChainHash("./chain.jsonl");
const result = await submitOts(chainHash);
if (result.success) {
  // result.proof contains the OTS calendar response
  console.log("Bitcoin anchor pending confirmation");
}
```

## Chain Export / Import

```typescript
// Export to JSON array
chain.export("./chain-export.json", { pretty: true });

// Export as JS objects
const entries = chain.exportJson();

// Import from JSON entries
const imported = Chain.fromJson(entries, { agent: "my-agent" });
```

## Verification

```typescript
import { verifyFile, verifyEntries } from "chain-of-consciousness";

// Verify a chain file (JSONL or JSON array format)
const result = verifyFile("./chain.jsonl");
console.log(result.valid);
console.log(result.errors);       // [] if valid
console.log(result.genesis_ts);   // ISO timestamp of inception
console.log(result.latest_ts);    // ISO timestamp of latest entry
console.log(result.agents);       // { "my-agent": 42 }
console.log(result.types);        // { genesis: 1, boot: 5, learn: 20, ... }
console.log(result.anchors);      // ["2026-03-17T...", ...]
console.log(result.schema_versions); // { "1.1": { first: 0, last: 41 } }
```

## Low-Level API

```typescript
import {
  sha256,
  computeDataHash,
  computeEntryHash,
  makeEntry,
  generateEd25519KeyPair,
  signEntry,
  verifySignature,
  buildRfc3161Tsq,
  parseTsrStatus,
} from "chain-of-consciousness";

// Hash computation
const hash = sha256("any string");
const dataHash = computeDataHash("event payload");
const entryHash = computeEntryHash(0, "2026-01-01T00:00:00Z", "genesis", "agent", dataHash, "0".repeat(64));

// Manual entry creation
const entry = makeEntry({
  sequence: 0,
  eventType: "genesis",
  data: "Manual genesis",
  prevHash: "0".repeat(64),
  agent: "my-agent",
});
```

## Event Types

| Type | Description |
|------|-------------|
| `genesis` | Agent inception (exactly one per chain) |
| `boot` | Agent session started |
| `session_start` | New session with bootstrap verification |
| `session_end` | Session ended with forward commitment |
| `learn` | Knowledge acquired |
| `decide` | Decision recorded |
| `create` | Content or artifact created |
| `milestone` | Achievement recorded |
| `rotate` | Cryptographic key rotated |
| `anchor` | External timestamp anchor recorded |
| `compaction` | Context window compacted |
| `governance` | Governance action recorded |
| `error` | Error event logged |
| `note` | General annotation |

## Hash Computation

Entry hashes use a pipe-delimited canonical form, matching the Python reference implementation:

```
entry_hash = SHA-256("{seq}|{timestamp}|{event_type}|{agent}|{data_hash}|{prev_hash}")
data_hash  = SHA-256(data_string)
```

Chains produced by either the Python or TypeScript implementation are cross-verifiable.

## Schema

Each entry in the JSONL file:

```json
{
  "seq": 0,
  "ts": "2026-04-21T12:00:00.000Z",
  "type": "genesis",
  "agent": "my-agent",
  "data": "Genesis block. Agent: my-agent.",
  "data_hash": "abc123...",
  "prev_hash": "0000...0000",
  "entry_hash": "def456...",
  "schema_version": "1.1",
  "signature": "ed25519_hex_signature (optional)"
}
```

## Deploy

1. Clone and build:
   ```bash
   git clone <repo>
   cd chain-of-consciousness
   npm install
   npm run build
   ```

2. Run tests:
   ```bash
   npm test
   ```

3. Use in your project:
   ```bash
   npm install chain-of-consciousness
   ```

## License

Apache 2.0 — AB Support LLC
