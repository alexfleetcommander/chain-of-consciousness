#!/usr/bin/env python3
"""
Chain of Consciousness — Cryptographic provenance log for AB Support Fleet.

Append-only SHA-256 hash chain recording agent lifecycle events.
Each entry links to the previous via prev_hash, creating a tamper-evident log.

Usage:
  python3 chain_of_consciousness.py --init          # Create genesis block
  python3 chain_of_consciousness.py --add --event-type boot --data "Session started, cycle 1"
  python3 chain_of_consciousness.py --add --event-type learn --data "Promoted 6 knowledge files"
  python3 chain_of_consciousness.py --add --event-type session_end --data "Session ending" --commitment "expected_state_hash"
  python3 chain_of_consciousness.py --add --event-type session_start --data "Session starting" --verification "actual_state_hash" --expected "previous_commitment_hash"
  python3 chain_of_consciousness.py --verify         # Verify full chain integrity (human-readable report)
  python3 chain_of_consciousness.py --verify --json  # Verify full chain integrity (JSON report)
  python3 chain_of_consciousness.py --status          # Show chain stats
  python3 chain_of_consciousness.py --tail N          # Show last N entries

Event types (Layer 1 Core):
  genesis, boot, learn, decide, create, milestone, rotate, anchor, error, note,
  session_start, session_end, compaction, governance
"""

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

CHAIN_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "chain")
CHAIN_FILE = os.path.join(CHAIN_DIR, "chain.jsonl")
META_FILE = os.path.join(CHAIN_DIR, "chain_meta.json")

SCHEMA_VERSION = "1.1"

VALID_EVENT_TYPES = [
    "genesis", "boot", "learn", "decide", "create",
    "milestone", "rotate", "anchor", "error", "note",
    "session_start", "session_end", "compaction", "governance"
]


def sha256(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def make_entry(sequence: int, event_type: str, data: str, prev_hash: str,
               agent: str = "alex", commitment: str = None,
               verification: str = None, commitment_match: bool = None) -> dict:
    ts = datetime.now(timezone.utc).isoformat()
    data_hash = sha256(data)
    # Entry hash = SHA-256(sequence|timestamp|event_type|agent|data_hash|prev_hash)
    # Hash computation is unchanged from v1.0 — backward compatible
    payload = f"{sequence}|{ts}|{event_type}|{agent}|{data_hash}|{prev_hash}"
    entry_hash = sha256(payload)
    entry = {
        "seq": sequence,
        "ts": ts,
        "type": event_type,
        "agent": agent,
        "data": data,
        "data_hash": data_hash,
        "prev_hash": prev_hash,
        "entry_hash": entry_hash,
        "schema_version": SCHEMA_VERSION
    }
    # Add optional forward-commitment fields
    if commitment is not None:
        entry["commitment"] = commitment
    if verification is not None:
        entry["verification"] = verification
    if commitment_match is not None:
        entry["commitment_match"] = commitment_match
    return entry


def read_chain() -> list:
    if not os.path.exists(CHAIN_FILE):
        return []
    entries = []
    with open(CHAIN_FILE, "r") as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries


def append_entry(entry: dict):
    os.makedirs(CHAIN_DIR, exist_ok=True)
    with open(CHAIN_FILE, "a") as f:
        f.write(json.dumps(entry, separators=(",", ":")) + "\n")


def update_meta(chain: list):
    meta = {
        "chain_length": len(chain),
        "genesis_hash": chain[0]["entry_hash"] if chain else None,
        "latest_hash": chain[-1]["entry_hash"] if chain else None,
        "latest_seq": chain[-1]["seq"] if chain else -1,
        "latest_ts": chain[-1]["ts"] if chain else None,
        "schema_version": SCHEMA_VERSION,
        "last_verified": None
    }
    with open(META_FILE, "w") as f:
        json.dump(meta, f, indent=2)


def find_last_commitment(chain: list) -> str:
    """Walk chain backward to find the most recent session_end commitment hash."""
    for entry in reversed(chain):
        if entry.get("type") == "session_end" and entry.get("commitment"):
            return entry["commitment"]
    return None


def verify_chain(chain: list) -> dict:
    """Verify full chain integrity. Returns a detailed report dict."""
    report = {
        "is_valid": False,
        "error": None,
        "genesis_ts": None,
        "latest_ts": None,
        "entry_count": len(chain),
        "agents": {},
        "types": {},
        "anchors": [],
        "session_bridges": 0,
        "session_mismatches": 0,
        "schema_versions": {},
    }

    if not chain:
        report["error"] = "Chain is empty"
        return report

    # Verify genesis
    if chain[0]["type"] != "genesis":
        report["error"] = f"Entry 0 is not genesis (type={chain[0]['type']})"
        return report
    if chain[0]["prev_hash"] != "0" * 64:
        report["error"] = "Genesis prev_hash is not zeros"
        return report

    report["genesis_ts"] = chain[0]["ts"]

    for i, entry in enumerate(chain):
        # Verify sequence
        if entry["seq"] != i:
            report["error"] = f"Entry {i}: sequence mismatch (expected {i}, got {entry['seq']})"
            return report

        # Verify data_hash
        expected_data_hash = sha256(entry["data"])
        if entry["data_hash"] != expected_data_hash:
            report["error"] = f"Entry {i}: data_hash mismatch"
            return report

        # Verify prev_hash linkage
        if i > 0 and entry["prev_hash"] != chain[i - 1]["entry_hash"]:
            report["error"] = f"Entry {i}: prev_hash doesn't match entry {i-1} hash"
            return report

        # Verify entry_hash
        payload = f"{entry['seq']}|{entry['ts']}|{entry['type']}|{entry['agent']}|{entry['data_hash']}|{entry['prev_hash']}"
        expected_hash = sha256(payload)
        if entry["entry_hash"] != expected_hash:
            report["error"] = f"Entry {i}: entry_hash mismatch (computed from stored fields)"
            return report

        # Collect stats
        etype = entry["type"]
        report["types"][etype] = report["types"].get(etype, 0) + 1
        agent = entry["agent"]
        report["agents"][agent] = report["agents"].get(agent, 0) + 1

        # Track anchors
        if etype == "anchor":
            report["anchors"].append(entry["ts"])

        # Track schema versions
        sv = entry.get("schema_version", "1.0")
        if sv not in report["schema_versions"]:
            report["schema_versions"][sv] = {"first": i, "last": i}
        else:
            report["schema_versions"][sv]["last"] = i

        # Track session bridges (session_start with verification)
        if etype == "session_start" and entry.get("verification"):
            report["session_bridges"] += 1
            if entry.get("commitment_match") is False:
                report["session_mismatches"] += 1

    report["latest_ts"] = chain[-1]["ts"]
    report["is_valid"] = True
    return report


def cmd_init(args):
    if os.path.exists(CHAIN_FILE):
        chain = read_chain()
        if chain:
            print(f"[WARN] Chain already exists with {len(chain)} entries.")
            print(f"       Genesis: {chain[0]['entry_hash'][:16]}...")
            print(f"       Use --add to append entries.")
            return

    genesis_data = (
        "GENESIS BLOCK — AB Support Fleet Chain of Consciousness. "
        "Agent: Alex (coordinator). Fleet: Alex, Bravo, Charlie. "
        "LLC: AB Support LLC (EIN: 99-2050022, NM). "
        "Domain: vibeagentmaking.com. "
        "Purpose: Tamper-evident provenance log proving continuous agent existence, "
        "learning, and decision-making. First entry in an unbroken chain. "
        f"Initialized: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}."
    )

    entry = make_entry(
        sequence=0,
        event_type="genesis",
        data=genesis_data,
        prev_hash="0" * 64,
        agent="alex"
    )
    append_entry(entry)
    update_meta([entry])

    print("=" * 60)
    print("  CHAIN OF CONSCIOUSNESS — GENESIS BLOCK")
    print("=" * 60)
    print(f"  Sequence:   0")
    print(f"  Timestamp:  {entry['ts']}")
    print(f"  Type:       genesis")
    print(f"  Entry Hash: {entry['entry_hash']}")
    print(f"  Data Hash:  {entry['data_hash']}")
    print(f"  Prev Hash:  {'0' * 16}... (zeros — first entry)")
    print(f"  Chain File: {CHAIN_FILE}")
    print("=" * 60)
    print(f"\n  The chain has begun.")


def cmd_add(args):
    chain = read_chain()
    if not chain:
        print("[ERROR] Chain not initialized. Run --init first.")
        sys.exit(1)

    event_type = args.event_type
    if event_type not in VALID_EVENT_TYPES:
        print(f"[ERROR] Invalid event type '{event_type}'. Valid: {', '.join(VALID_EVENT_TYPES)}")
        sys.exit(1)

    data = args.data
    if not data:
        print("[ERROR] --data is required.")
        sys.exit(1)

    agent = args.agent or "alex"
    prev_hash = chain[-1]["entry_hash"]
    seq = len(chain)

    # Handle forward-commitment fields
    commitment = None
    verification = None
    commitment_match = None

    if event_type == "session_end" and args.commitment:
        # Validate commitment looks like a hex hash (basic sanity)
        c = args.commitment.strip()
        if len(c) != 64 or not all(ch in "0123456789abcdef" for ch in c):
            print(f"[ERROR] --commitment must be a 64-character lowercase hex SHA-256 hash.")
            sys.exit(1)
        commitment = c

    if event_type == "session_start" and args.verification:
        v = args.verification.strip()
        if len(v) != 64 or not all(ch in "0123456789abcdef" for ch in v):
            print(f"[ERROR] --verification must be a 64-character lowercase hex SHA-256 hash.")
            sys.exit(1)
        verification = v

        # Check against expected commitment
        if args.expected:
            e = args.expected.strip()
            if len(e) != 64 or not all(ch in "0123456789abcdef" for ch in e):
                print(f"[ERROR] --expected must be a 64-character lowercase hex SHA-256 hash.")
                sys.exit(1)
            commitment_match = (verification == e)
        else:
            # Auto-find last session_end commitment in chain
            last_commitment = find_last_commitment(chain)
            if last_commitment:
                commitment_match = (verification == last_commitment)
            # If no previous commitment found, leave commitment_match as None

    # Warn if commitment/verification used on wrong event type
    if args.commitment and event_type != "session_end":
        print(f"[WARN] --commitment is only meaningful on session_end events (ignored).")
    if args.verification and event_type != "session_start":
        print(f"[WARN] --verification is only meaningful on session_start events (ignored).")

    entry = make_entry(seq, event_type, data, prev_hash, agent,
                       commitment=commitment, verification=verification,
                       commitment_match=commitment_match)
    append_entry(entry)
    chain.append(entry)
    update_meta(chain)

    print(f"[+] Entry #{seq} ({event_type}) added. Hash: {entry['entry_hash'][:16]}...")
    if commitment:
        print(f"    Forward commitment: {commitment[:16]}...")
    if verification:
        match_str = "MATCH" if commitment_match else ("MISMATCH" if commitment_match is False else "no prior commitment")
        print(f"    Bootstrap verification: {verification[:16]}... ({match_str})")


def cmd_verify(args):
    chain = read_chain()
    report = verify_chain(chain)

    if report["is_valid"]:
        # Update meta with verification timestamp
        if os.path.exists(META_FILE):
            with open(META_FILE, "r") as f:
                meta = json.load(f)
            meta["last_verified"] = datetime.now(timezone.utc).isoformat()
            with open(META_FILE, "w") as f:
                json.dump(meta, f, indent=2)

    if args.json:
        # Machine-readable JSON output
        print(json.dumps(report, indent=2))
        if not report["is_valid"]:
            sys.exit(1)
        return

    # Human-readable provenance report
    if not report["is_valid"]:
        print(f"[FAIL] Chain verification failed: {report['error']}")
        sys.exit(1)

    genesis_ts = report["genesis_ts"][:20] + "Z" if report["genesis_ts"] else "N/A"
    latest_ts = report["latest_ts"][:20] + "Z" if report["latest_ts"] else "N/A"
    agents_str = ", ".join(f"{k}({v})" for k, v in sorted(report["agents"].items(), key=lambda x: -x[1]))
    anchor_count = len(report["anchors"])
    anchor_latest = report["anchors"][-1][:20] + "Z" if report["anchors"] else "none"

    # Schema version ranges
    sv_parts = []
    for sv, rng in sorted(report["schema_versions"].items()):
        if rng["first"] == rng["last"]:
            sv_parts.append(f"{sv} (entry {rng['first']})")
        else:
            sv_parts.append(f"{sv} (entries {rng['first']}-{rng['last']})")
    sv_str = ", ".join(sv_parts)

    print("Chain of Consciousness — Verification Report")
    print("=" * 46)
    print(f"Genesis:    {genesis_ts}")
    print(f"Latest:     {latest_ts}")
    print(f"Entries:    {report['entry_count']}")
    print(f"Integrity:  VALID (all hashes verified)")
    print(f"Agents:     {agents_str}")
    print(f"Anchors:    {anchor_count} (latest: {anchor_latest})")
    print(f"Session continuity: {report['session_bridges']} session bridges, {report['session_mismatches']} mismatches")
    print(f"Schema versions: {sv_str}")


def cmd_status(args):
    chain = read_chain()
    if not chain:
        print("[INFO] Chain not initialized. Run --init first.")
        return

    types = {}
    agents = {}
    for e in chain:
        types[e["type"]] = types.get(e["type"], 0) + 1
        agents[e["agent"]] = agents.get(e["agent"], 0) + 1

    print(f"Chain: {len(chain)} entries")
    print(f"Genesis: {chain[0]['ts'][:19]}Z")
    print(f"Latest:  {chain[-1]['ts'][:19]}Z (seq {chain[-1]['seq']})")
    print(f"Types:   {', '.join(f'{k}({v})' for k,v in sorted(types.items()))}")
    print(f"Agents:  {', '.join(f'{k}({v})' for k,v in sorted(agents.items()))}")


def cmd_anchor(args):
    """Create an OpenTimestamps anchor for the current chain state.

    Computes SHA-256 of the full chain file and queues a host command to submit
    the digest to OpenTimestamps calendar servers. The returned .ots proof file
    proves the chain existed at a specific time, anchored to the Bitcoin blockchain.

    The proof takes a few hours to fully confirm (waiting for a Bitcoin block),
    but the submission is instant and the calendar receipt is immediately useful.
    """
    if not os.path.exists(CHAIN_FILE):
        print("[ERROR] Chain file not found. Run --init first.")
        sys.exit(1)

    # Compute SHA-256 of the full chain file
    with open(CHAIN_FILE, "rb") as f:
        chain_bytes = f.read()
    chain_hash = hashlib.sha256(chain_bytes).hexdigest()

    chain = read_chain()
    seq = chain[-1]["seq"] if chain else 0

    # Save anchor metadata
    anchor_dir = os.path.join(CHAIN_DIR, "anchors")
    os.makedirs(anchor_dir, exist_ok=True)

    anchor_id = f"anchor_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    anchor_meta = {
        "id": anchor_id,
        "chain_hash": chain_hash,
        "chain_length": len(chain),
        "latest_seq": seq,
        "latest_entry_hash": chain[-1]["entry_hash"] if chain else None,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": "pending",
        "ots_proof_file": None
    }

    anchor_meta_path = os.path.join(anchor_dir, f"{anchor_id}.json")
    with open(anchor_meta_path, "w") as f:
        json.dump(anchor_meta, f, indent=2)

    # Queue host command to submit hash to OpenTimestamps calendar servers
    # The OTS client accepts a file and timestamps it. We'll create a .hash file
    # containing just the chain hash, then submit it.
    chain_win_path = r"C:\Users\BB\Desktop\ITbot\chain"
    hash_file = os.path.join(anchor_dir, f"{anchor_id}.hash")
    with open(hash_file, "w") as f:
        f.write(chain_hash)

    # Write command queue entry for host to execute OTS stamp
    # Uses curl to POST directly to OTS calendar server (no client library needed)
    command_queue_path = os.path.join(os.path.dirname(CHAIN_DIR), "command_queue.json")

    # Build PowerShell command to submit hash to OTS calendar servers
    ots_command = (
        f'powershell -Command "'
        f"$hash = '{chain_hash}'; "
        f"$bytes = [byte[]]::new(32); "
        f"for($i=0;$i -lt 32;$i++){{ $bytes[$i] = [Convert]::ToByte($hash.Substring($i*2,2),16) }}; "
        f"$resp = Invoke-WebRequest -Uri 'https://a.pool.opentimestamps.org/digest' -Method POST -Body $bytes -ContentType 'application/x-www-form-urlencoded' -OutFile '{chain_win_path}\\anchors\\{anchor_id}.ots'; "
        f"if(Test-Path '{chain_win_path}\\anchors\\{anchor_id}.ots'){{ Write-Host 'OTS proof saved: {anchor_id}.ots' }} else {{ Write-Host 'OTS submission failed' }}"
        f'"'
    )

    # Read existing queue or create new
    existing_commands = []
    if os.path.exists(command_queue_path):
        try:
            with open(command_queue_path, "r") as f:
                existing_commands = json.load(f).get("commands", [])
        except Exception:
            existing_commands = []

    existing_commands.append({
        "id": f"ots_{anchor_id}",
        "command": ots_command,
        "description": f"Submit chain hash to OpenTimestamps (seq {seq}, {len(chain)} entries)"
    })

    with open(command_queue_path, "w") as f:
        json.dump({"commands": existing_commands}, f, indent=2)

    # Also add an anchor entry to the chain itself
    anchor_entry = make_entry(
        sequence=len(chain),
        event_type="anchor",
        data=f"OpenTimestamps anchor submitted. Chain hash: {chain_hash[:16]}... (seq 0-{seq}, {len(chain)} entries). Proof pending Bitcoin confirmation.",
        prev_hash=chain[-1]["entry_hash"],
        agent="alex"
    )
    append_entry(anchor_entry)
    chain.append(anchor_entry)
    update_meta(chain)

    print(f"[ANCHOR] Chain hash: {chain_hash}")
    print(f"[ANCHOR] Entries: {len(chain)-1} (before anchor entry)")
    print(f"[ANCHOR] Latest seq: {seq}")
    print(f"[ANCHOR] OTS submission queued for host execution")
    print(f"[ANCHOR] Proof will be saved to: chain/anchors/{anchor_id}.ots")
    print(f"[ANCHOR] Note: Bitcoin confirmation takes 1-12 hours. Calendar receipt is immediate.")


def cmd_tail(args):
    chain = read_chain()
    n = args.n or 5
    for entry in chain[-n:]:
        extra = ""
        if entry.get("commitment"):
            extra += f" [commitment: {entry['commitment'][:12]}...]"
        if entry.get("verification"):
            match = "match" if entry.get("commitment_match") else ("MISMATCH" if entry.get("commitment_match") is False else "?")
            extra += f" [verify: {entry['verification'][:12]}... {match}]"
        sv = entry.get("schema_version", "1.0")
        print(f"  #{entry['seq']:>4} [{entry['type']:>13}] {entry['ts'][:19]}Z | {entry['data'][:70]}{extra}")


def main():
    parser = argparse.ArgumentParser(description="Chain of Consciousness — Agent Provenance Log")
    parser.add_argument("--init", action="store_true", help="Create genesis block")
    parser.add_argument("--add", action="store_true", help="Add entry to chain")
    parser.add_argument("--verify", action="store_true", help="Verify chain integrity")
    parser.add_argument("--anchor", action="store_true", help="Submit chain hash to OpenTimestamps for Bitcoin anchoring")
    parser.add_argument("--status", action="store_true", help="Show chain stats")
    parser.add_argument("--tail", action="store_true", help="Show last N entries")
    parser.add_argument("--event-type", type=str, help="Event type for --add")
    parser.add_argument("--data", type=str, help="Event data for --add")
    parser.add_argument("--agent", type=str, default="alex", help="Agent name (default: alex)")
    parser.add_argument("-n", type=int, default=5, help="Number of entries for --tail")
    # Forward-commitment flags
    parser.add_argument("--commitment", type=str, default=None,
                        help="Forward-commitment hash for session_end (SHA-256 of expected bootstrap state)")
    parser.add_argument("--verification", type=str, default=None,
                        help="Bootstrap verification hash for session_start (SHA-256 of actual bootstrap state)")
    parser.add_argument("--expected", type=str, default=None,
                        help="Expected commitment hash to compare against verification (auto-detected from chain if omitted)")
    # Verification output format
    parser.add_argument("--json", action="store_true", help="Output verification report as JSON")

    args = parser.parse_args()

    if args.init:
        cmd_init(args)
    elif args.add:
        cmd_add(args)
    elif args.verify:
        cmd_verify(args)
    elif args.anchor:
        cmd_anchor(args)
    elif args.status:
        cmd_status(args)
    elif args.tail:
        cmd_tail(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
