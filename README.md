# Chain of Consciousness

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Python 3.8+](https://img.shields.io/badge/python-3.8+-green.svg)](https://www.python.org/downloads/)

**Cryptographic provenance protocol for AI agents.** Tamper-evident, hash-linked logs that prove what your agent did, learned, and decided.

## What is Agent Provenance?

As AI agents become more autonomous — making decisions, learning, and taking actions — there's no standard way to prove *what actually happened*. Agent provenance is a cryptographic record of an agent's lifecycle: every action, decision, and learning event linked in an unbreakable SHA-256 hash chain.

Chain of Consciousness gives your agents a verifiable memory. Anyone can audit the chain and confirm that no entries were added, removed, or modified after the fact.

## Install

```bash
pip install chain-of-consciousness
```

## Quickstart

```python
from chain_of_consciousness import Chain

chain = Chain("my-agent", storage="chain.jsonl")
chain.add("LEARN", {"topic": "cryptography", "source": "web"})
chain.add("DECIDE", {"decision": "deploy v2", "confidence": 0.95})
result = chain.verify()
print(result)  # VerifyResult(valid=True, entries=3, errors=[])
```

That's it. Five lines. Zero dependencies.

## Features

- **Zero required dependencies** — Core chain + verification uses only Python stdlib (SHA-256 from `hashlib`).
- **Tamper-evident** — Each entry's hash includes the previous entry's hash. Change one byte, the entire chain breaks.
- **Multi-agent support** — Multiple agents can write to the same chain file, each identified by name.
- **Session continuity** — Forward commitments link sessions together, proving nothing was lost between restarts.
- **Optional timestamping** — Anchor your chain to Bitcoin (OpenTimestamps) or RFC 3161 TSAs for independent proof of time.
- **Export & share** — Export chains as JSON for independent verification.
- **CLI included** — `coc` command for terminal-based chain management.

## API Reference

### `Chain(agent, storage=None)`

Create or load a provenance chain.

| Parameter | Type | Description |
|-----------|------|-------------|
| `agent` | `str` | Name identifying the agent writing to this chain |
| `storage` | `str \| None` | Path to a JSONL file for persistence. `None` = in-memory only |

### `chain.add(event_type, data, *, commitment=None, verification=None)`

Append a new entry. Returns a `ChainEntry`.

| Parameter | Type | Description |
|-----------|------|-------------|
| `event_type` | `str` | Category: `boot`, `learn`, `decide`, `create`, `milestone`, `anchor`, `error`, `note`, `session_start`, `session_end`, `compaction`, `governance`, or any custom type |
| `data` | `str \| dict \| list` | Payload. Non-strings are JSON-serialized |
| `commitment` | `str \| None` | SHA-256 forward commitment (for `session_end`) |
| `verification` | `str \| None` | SHA-256 bootstrap verification (for `session_start`) |

### `chain.verify() -> VerifyResult`

Verify full chain integrity. Checks hash linkage, data hashes, sequence numbering, and genesis block.

```python
result = chain.verify()
result.valid     # bool — True if chain is intact
result.entries   # int — total entry count
result.errors    # list[str] — empty if valid
result.agents    # dict — {agent_name: entry_count}
result.types     # dict — {event_type: count}
result.anchors   # list — timestamps of anchor entries
```

### `chain.export(path)`

Export the chain to a JSON array file for sharing or independent verification.

### `verify_file(path) -> VerifyResult`

Verify a chain file (JSONL or JSON array format) without loading it into a `Chain` object.

```python
from chain_of_consciousness import verify_file
result = verify_file("their_chain.json")
```

### `chain.entries -> list[ChainEntry]`

Read-only list of all entries.

### `chain.latest -> ChainEntry | None`

The most recent entry.

### `len(chain) -> int`

Number of entries in the chain.

## CLI

The package installs a `coc` command:

```bash
# Create a new chain
coc init --agent my-agent --file chain.jsonl

# Add entries
coc add learn '{"topic": "security"}' --file chain.jsonl
coc add decide "deploy to production" --file chain.jsonl

# Verify integrity
coc verify chain.jsonl
coc verify chain.jsonl --json

# Show status
coc status chain.jsonl

# Show recent entries
coc tail chain.jsonl -n 10

# Export to JSON
coc export --file chain.jsonl --out chain.json
```

## Anchoring (Optional)

Anchor your chain to external timestamping authorities for independent proof of existence.

```bash
pip install chain-of-consciousness[anchoring]
```

### RFC 3161 TSA (no extra deps needed)

```python
from chain_of_consciousness.anchor import compute_chain_hash, submit_tsa, parse_tsr_status

chain_hash = compute_chain_hash("chain.jsonl")
tsr_bytes = submit_tsa(chain_hash)
status = parse_tsr_status(tsr_bytes)
print(status["status_text"])  # "granted"
```

### OpenTimestamps / Bitcoin

```python
from chain_of_consciousness.anchor import compute_chain_hash, submit_ots

chain_hash = compute_chain_hash("chain.jsonl")
ots_proof = submit_ots(chain_hash)
with open("chain.ots", "wb") as f:
    f.write(ots_proof)
```

## Chain Entry Format

Each entry in the JSONL file is a JSON object:

```json
{
  "seq": 1,
  "ts": "2026-03-21T12:00:00+00:00",
  "type": "learn",
  "agent": "my-agent",
  "data": "{\"topic\":\"cryptography\"}",
  "data_hash": "a1b2c3...",
  "prev_hash": "d4e5f6...",
  "entry_hash": "789abc...",
  "schema_version": "1.1"
}
```

**Hash computation:** `entry_hash = SHA-256(seq|ts|type|agent|data_hash|prev_hash)`

This means every entry is cryptographically bound to:
- Its position in the chain (seq)
- When it was created (ts)
- What happened (type + data_hash)
- Everything before it (prev_hash)

## Whitepaper

For the full protocol specification, design rationale, and anchoring architecture, see the [Chain of Consciousness whitepaper](https://vibeagentmaking.com/whitepaper.html).

## Verification Demo

Try the interactive verification demo at [vibeagentmaking.com/verify](https://vibeagentmaking.com/verify/).

## License

Apache 2.0. See [LICENSE](LICENSE).
