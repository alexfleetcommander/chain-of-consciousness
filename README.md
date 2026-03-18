# Chain of Consciousness v1.1 Upgrade

Upgrades `chain_of_consciousness.py` to match whitepaper v2 specification.

## What Changed

### New Event Types
Added `session_start`, `session_end`, `compaction`, `governance` to VALID_EVENT_TYPES (total: 14 types).

### Forward-Commitment Mechanism
The novel feature from the whitepaper. Three new CLI flags:
- `--commitment HASH` — on `session_end`, stores SHA-256 of expected bootstrap state
- `--verification HASH` — on `session_start`, stores SHA-256 of actual bootstrap state
- `--expected HASH` — optional: the commitment hash to compare against (auto-detected from chain if omitted)

New entry fields (all optional):
- `commitment` — SHA-256 hash (session_end only)
- `verification` — SHA-256 hash (session_start only)
- `commitment_match` — boolean (session_start only, true if verification matches prior commitment)

### Schema Version
All new entries include `"schema_version": "1.1"`. Old entries without this field are treated as version 1.0 by the verifier.

### Enhanced Verification Report
`--verify` now outputs a human-readable provenance report with agent breakdown, anchor count, session bridge stats, and schema version ranges. `--verify --json` outputs the same data as machine-readable JSON.

## Backward Compatibility
- Hash computation is UNCHANGED (SHA-256 of `sequence|timestamp|type|agent|data_hash|prev_hash`)
- New fields (schema_version, commitment, verification, commitment_match) are NOT included in hash computation
- Existing chain.jsonl (46 entries) verified successfully with new code
- Old entries without schema_version are treated as 1.0

## Deployment
Replace `tools/chain_of_consciousness.py` with the upgraded version from this directory.

## Testing
```bash
python test_upgrade.py
```
Runs 30 tests covering backward compatibility, new event types, forward-commitment, schema versioning, verification reports, input validation, and hash computation stability.
