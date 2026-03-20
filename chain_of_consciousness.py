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
  python3 chain_of_consciousness.py --verify           # Verify full chain integrity (human-readable report)
  python3 chain_of_consciousness.py --verify --json    # Verify full chain integrity (JSON report)
  python3 chain_of_consciousness.py --verify-tsa       # Verify RFC 3161 TSA anchors
  python3 chain_of_consciousness.py --status            # Show chain stats
  python3 chain_of_consciousness.py --tail N            # Show last N entries

Event types (Layer 1 Core):
  genesis, boot, learn, decide, create, milestone, rotate, anchor, error, note,
  session_start, session_end, compaction, governance
"""

import argparse
import hashlib
import json
import os
import secrets
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


# ── RFC 3161 TSA helpers ──────────────────────────────────────────────

def _der_tag_length(tag: int, content: bytes) -> bytes:
    """Wrap content bytes with a DER tag-length header."""
    length = len(content)
    if length < 0x80:
        return bytes([tag, length]) + content
    elif length < 0x100:
        return bytes([tag, 0x81, length]) + content
    else:
        return bytes([tag, 0x82, (length >> 8) & 0xff, length & 0xff]) + content


def build_rfc3161_tsq(hash_bytes: bytes) -> bytes:
    """Build a DER-encoded RFC 3161 TimeStampReq for a SHA-256 digest.

    Structure per RFC 3161 Section 2.4.1:
      TimeStampReq ::= SEQUENCE {
          version INTEGER {v1(1)}, messageImprint MessageImprint,
          nonce INTEGER OPTIONAL, certReq BOOLEAN DEFAULT FALSE }
    """
    # SHA-256 OID: 2.16.840.1.101.3.4.2.1
    sha256_oid = _der_tag_length(0x06, bytes([
        0x60, 0x86, 0x48, 0x01, 0x65, 0x03, 0x04, 0x02, 0x01
    ]))
    alg_id = _der_tag_length(0x30, sha256_oid + bytes([0x05, 0x00]))  # SEQUENCE { OID, NULL }
    msg_imprint = _der_tag_length(0x30, alg_id + _der_tag_length(0x04, hash_bytes))

    version = _der_tag_length(0x02, bytes([0x01]))  # INTEGER v1

    # Random nonce for replay protection (positive integer)
    nonce_raw = secrets.token_bytes(8)
    if nonce_raw[0] & 0x80:
        nonce_raw = b'\x00' + nonce_raw
    nonce = _der_tag_length(0x02, nonce_raw)

    cert_req = _der_tag_length(0x01, bytes([0xff]))  # BOOLEAN TRUE

    return _der_tag_length(0x30, version + msg_imprint + nonce + cert_req)


def parse_tsr_status(tsr_bytes: bytes) -> dict:
    """Parse an RFC 3161 TimeStampResp to extract status info.

    Returns dict with status code, text, token presence, and size.
    For full cryptographic verification use: openssl ts -verify
    """
    STATUS_NAMES = {
        0: "granted", 1: "grantedWithMods", 2: "rejection",
        3: "waiting", 4: "revocationWarning", 5: "revocationNotification"
    }

    def read_tl(data, off):
        """Read DER tag and length. Returns (tag, length, data_start_offset)."""
        tag = data[off]; off += 1
        lb = data[off]; off += 1
        if lb < 0x80:
            return tag, lb, off
        n = lb & 0x7f
        length = 0
        for _ in range(n):
            length = (length << 8) | data[off]; off += 1
        return tag, length, off

    try:
        # Outer SEQUENCE (TimeStampResp)
        _, outer_len, outer_start = read_tl(tsr_bytes, 0)
        outer_end = outer_start + outer_len

        # PKIStatusInfo SEQUENCE
        _, si_len, si_start = read_tl(tsr_bytes, outer_start)
        si_end = si_start + si_len

        # PKIStatus INTEGER
        tag, int_len, int_start = read_tl(tsr_bytes, si_start)
        if tag != 0x02:
            return {"status": -1, "status_text": "parse_error: expected INTEGER",
                    "has_token": False, "tsr_size": len(tsr_bytes)}
        status_val = int.from_bytes(tsr_bytes[int_start:int_start + int_len], "big", signed=True)

        return {
            "status": status_val,
            "status_text": STATUS_NAMES.get(status_val, f"unknown({status_val})"),
            "has_token": si_end < outer_end,  # TimeStampToken follows PKIStatusInfo
            "tsr_size": len(tsr_bytes),
        }
    except (IndexError, ValueError) as e:
        return {"status": -1, "status_text": f"parse_error: {e}",
                "has_token": False, "tsr_size": len(tsr_bytes)}


def submit_tsa(hash_bytes: bytes, server_url: str, timeout: int = 30) -> bytes:
    """Submit a SHA-256 digest to an RFC 3161 TSA server. Returns raw TSR bytes."""
    import urllib.request
    import ssl

    tsq = build_rfc3161_tsq(hash_bytes)
    req = urllib.request.Request(
        server_url,
        data=tsq,
        headers={
            "Content-Type": "application/timestamp-query",
            "User-Agent": "chain-of-consciousness/1.1",
        },
        method="POST",
    )
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        return resp.read()


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

    # Summarize TSA anchors if any exist
    anchor_dir = os.path.join(CHAIN_DIR, "anchors")
    tsa_count = 0
    if os.path.isdir(anchor_dir):
        tsa_count = sum(1 for f in os.listdir(anchor_dir) if f.endswith(".tsr"))
    if tsa_count > 0:
        print(f"TSA proofs:  {tsa_count} (use --verify-tsa for details)")


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

    # Submit hash to OpenTimestamps calendar servers.
    # Uses the opentimestamps library (pip install opentimestamps) to create
    # proper DetachedTimestampFile format that can be verified with `ots verify`.
    # Falls back to raw urllib if library is not installed.
    hash_bytes = bytes.fromhex(chain_hash)
    ots_proof_path = os.path.join(anchor_dir, f"{anchor_id}.ots")
    ots_success = False

    import urllib.request
    import ssl
    import io as _io

    # Calendar servers to submit to
    ots_servers = [
        "https://a.pool.opentimestamps.org",
        "https://b.pool.opentimestamps.org",
        "https://a.pool.eternitywall.com",
    ]

    try:
        # Preferred method: use opentimestamps library for proper .ots format
        from opentimestamps.core.timestamp import DetachedTimestampFile, Timestamp
        from opentimestamps.core.op import OpSHA256
        from opentimestamps.core.serialize import (
            StreamSerializationContext, StreamDeserializationContext
        )

        timestamp = Timestamp(hash_bytes)
        detached = DetachedTimestampFile(OpSHA256(), timestamp)
        ssl_ctx = ssl.create_default_context()
        calendars_merged = 0

        for server_url in ots_servers:
            try:
                req = urllib.request.Request(
                    f"{server_url}/digest",
                    data=hash_bytes,
                    headers={
                        "Content-Type": "application/x-www-form-urlencoded",
                        "User-Agent": "chain-of-consciousness/1.1",
                        "Accept": "application/vnd.opentimestamps.v1",
                    },
                    method="POST"
                )
                with urllib.request.urlopen(req, timeout=30, context=ssl_ctx) as resp:
                    response_data = resp.read()
                if len(response_data) > 0:
                    resp_buf = _io.BytesIO(response_data)
                    resp_ctx = StreamDeserializationContext(resp_buf)
                    cal_ts = Timestamp.deserialize(resp_ctx, hash_bytes)
                    timestamp.merge(cal_ts)
                    calendars_merged += 1
                    print(f"[ANCHOR] Calendar {server_url}: {len(response_data)} bytes, merged OK")
            except Exception as e:
                print(f"[ANCHOR] Calendar {server_url} failed: {e}")
                continue

        if calendars_merged > 0:
            # Serialize proper DetachedTimestampFile
            buf = _io.BytesIO()
            ser_ctx = StreamSerializationContext(buf)
            detached.serialize(ser_ctx)
            ots_data = buf.getvalue()
            with open(ots_proof_path, "wb") as pf:
                pf.write(ots_data)
            ots_success = True
            anchor_meta["status"] = "calendar_submitted_proper"
            anchor_meta["ots_proof_file"] = f"{anchor_id}.ots"
            anchor_meta["ots_proof_size"] = len(ots_data)
            anchor_meta["calendars_submitted"] = calendars_merged
            anchor_meta["ots_format"] = "DetachedTimestampFile"
            with open(anchor_meta_path, "w") as mf:
                json.dump(anchor_meta, mf, indent=2)
            print(f"[ANCHOR] Proper .ots file saved: {len(ots_data)} bytes, {calendars_merged} calendar(s)")

    except ImportError:
        # Fallback: raw urllib submission (saves calendar response directly)
        # These files need post-processing with ots_fix_and_verify.py
        print("[ANCHOR] WARNING: opentimestamps library not installed. Using raw fallback.")
        print("[ANCHOR] Install with: pip install opentimestamps")
        for server_url in ots_servers:
            try:
                req = urllib.request.Request(
                    f"{server_url}/digest",
                    data=hash_bytes,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    method="POST"
                )
                ctx = ssl.create_default_context()
                with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
                    proof_data = resp.read()
                    if len(proof_data) > 0:
                        with open(ots_proof_path, "wb") as pf:
                            pf.write(proof_data)
                        ots_success = True
                        anchor_meta["status"] = "submitted_raw"
                        anchor_meta["ots_proof_file"] = f"{anchor_id}.ots"
                        anchor_meta["ots_proof_size"] = len(proof_data)
                        anchor_meta["ots_server"] = server_url
                        anchor_meta["ots_format"] = "raw_calendar_response"
                        with open(anchor_meta_path, "w") as mf:
                            json.dump(anchor_meta, mf, indent=2)
                        print(f"[ANCHOR] Raw calendar response: {len(proof_data)} bytes from {server_url}")
                        break
            except Exception as e:
                print(f"[ANCHOR] Server {server_url} failed: {e}")
                continue

    if not ots_success:
        print(f"[ANCHOR] WARNING: All OTS servers failed. Hash saved locally but not yet anchored.")
        print(f"[ANCHOR] You can retry later with: python3 {sys.argv[0]} --anchor")

    # ── Tier 2: RFC 3161 TSA ─────────────────────────────────────
    tsa_success = False
    tsa_servers = [
        ("freeTSA", "https://freetsa.org/tsr"),
    ]

    for tsa_name, tsa_url in tsa_servers:
        try:
            tsr_bytes = submit_tsa(hash_bytes, tsa_url)
            tsr_info = parse_tsr_status(tsr_bytes)
            if tsr_info["status"] in (0, 1):  # granted or grantedWithMods
                tsr_path = os.path.join(anchor_dir, f"{anchor_id}.tsr")
                with open(tsr_path, "wb") as tf:
                    tf.write(tsr_bytes)
                tsa_success = True
                anchor_meta["tsa_status"] = tsr_info["status_text"]
                anchor_meta["tsa_server"] = tsa_url
                anchor_meta["tsa_proof_file"] = f"{anchor_id}.tsr"
                anchor_meta["tsa_proof_size"] = tsr_info["tsr_size"]
                anchor_meta["tsa_has_token"] = tsr_info["has_token"]
                with open(anchor_meta_path, "w") as mf:
                    json.dump(anchor_meta, mf, indent=2)
                print(f"[ANCHOR] TSA ({tsa_name}): {tsr_info['status_text']}, "
                      f"{tsr_info['tsr_size']} bytes, token={'yes' if tsr_info['has_token'] else 'no'}")
                break
            else:
                print(f"[ANCHOR] TSA ({tsa_name}): rejected — status={tsr_info['status_text']}")
        except Exception as e:
            print(f"[ANCHOR] TSA ({tsa_name}) failed: {e}")

    if not tsa_success:
        print(f"[ANCHOR] WARNING: TSA submission failed. OTS-only anchor.")
        anchor_meta["tsa_status"] = "failed"
        with open(anchor_meta_path, "w") as mf:
            json.dump(anchor_meta, mf, indent=2)

    # Also save the hash in a text file for manual verification
    hash_file = os.path.join(anchor_dir, f"{anchor_id}.hash")
    with open(hash_file, "w") as f:
        f.write(chain_hash)

    # Build anchor entry reflecting both tiers
    tiers = []
    if ots_success:
        tiers.append("OTS/Bitcoin")
    if tsa_success:
        tiers.append("RFC3161/TSA")
    tier_str = " + ".join(tiers) if tiers else "local-only"

    anchor_entry = make_entry(
        sequence=len(chain),
        event_type="anchor",
        data=f"Anchor submitted ({tier_str}). Chain hash: {chain_hash[:16]}... (seq 0-{seq}, {len(chain)} entries). Proof pending confirmation.",
        prev_hash=chain[-1]["entry_hash"],
        agent="alex"
    )
    append_entry(anchor_entry)
    chain.append(anchor_entry)
    update_meta(chain)

    print(f"[ANCHOR] Chain hash: {chain_hash}")
    print(f"[ANCHOR] Entries: {len(chain)-1} (before anchor entry)")
    print(f"[ANCHOR] Latest seq: {seq}")
    if ots_success:
        print(f"[ANCHOR] OTS proof saved: chain/anchors/{anchor_id}.ots")
        print(f"[ANCHOR] Bitcoin confirmation takes 1-12 hours. Calendar receipt is immediate.")
        print(f"[ANCHOR] To check Bitcoin status later: python3 tools/ots_upgrade_check.py")
    else:
        print(f"[ANCHOR] OTS submission failed — hash saved locally for retry.")
    if tsa_success:
        print(f"[ANCHOR] TSA proof saved: chain/anchors/{anchor_id}.tsr")
        print(f"[ANCHOR] Full TSA verification: openssl ts -verify -in chain/anchors/{anchor_id}.tsr "
              f"-data {CHAIN_FILE} -CAfile cacert.pem")
    else:
        print(f"[ANCHOR] TSA submission failed — OTS is sole external anchor.")


def cmd_verify_tsa(args):
    """Verify RFC 3161 TSA anchors.

    Parses saved .tsr files, checks status, and compares chain hashes
    against the current chain file. For full TSA signature verification:
      openssl ts -verify -in <tsr> -data chain.jsonl -CAfile cacert.pem
    """
    anchor_dir = os.path.join(CHAIN_DIR, "anchors")
    if not os.path.isdir(anchor_dir):
        print("[INFO] No anchors directory found.")
        return

    anchor_files = sorted(
        [f for f in os.listdir(anchor_dir) if f.startswith("anchor_") and f.endswith(".json")],
        reverse=True
    )
    if not anchor_files:
        print("[INFO] No anchor metadata files found.")
        return

    # Compute current chain hash for comparison
    current_hash = None
    if os.path.exists(CHAIN_FILE):
        with open(CHAIN_FILE, "rb") as f:
            current_hash = hashlib.sha256(f.read()).hexdigest()

    tsa_found = 0
    tsa_valid = 0

    print("RFC 3161 TSA Anchor Verification")
    print("=" * 40)

    for af in anchor_files:
        meta_path = os.path.join(anchor_dir, af)
        with open(meta_path, "r") as f:
            meta = json.load(f)

        tsr_file = meta.get("tsa_proof_file")
        if not tsr_file:
            continue

        tsa_found += 1
        anchor_id = meta.get("id", af.replace(".json", ""))
        tsr_path = os.path.join(anchor_dir, tsr_file)

        if not os.path.exists(tsr_path):
            print(f"  {anchor_id}: TSR file missing ({tsr_file})")
            continue

        with open(tsr_path, "rb") as f:
            tsr_bytes = f.read()

        tsr_info = parse_tsr_status(tsr_bytes)

        chain_match = "N/A"
        if current_hash and meta.get("chain_hash"):
            chain_match = "CURRENT" if current_hash == meta["chain_hash"] else "STALE (chain has grown)"

        status_ok = tsr_info["status"] in (0, 1)
        if status_ok:
            tsa_valid += 1

        print(f"  {anchor_id}:")
        print(f"    TSA server:  {meta.get('tsa_server', 'unknown')}")
        print(f"    Status:      {tsr_info['status_text']} ({'OK' if status_ok else 'FAILED'})")
        print(f"    Token:       {'present' if tsr_info['has_token'] else 'absent'}")
        print(f"    TSR size:    {tsr_info['tsr_size']} bytes")
        print(f"    Chain hash:  {meta.get('chain_hash', 'unknown')[:16]}... ({chain_match})")
        print(f"    Timestamp:   {meta.get('timestamp', 'unknown')}")

    if args.json:
        report = {
            "tsa_anchors_found": tsa_found,
            "tsa_anchors_valid": tsa_valid,
            "current_chain_hash": current_hash,
        }
        print(json.dumps(report, indent=2))
    else:
        print(f"\nTSA Anchors: {tsa_found} found, {tsa_valid} valid")
        if tsa_found > 0:
            print(f"Full signature verification: openssl ts -verify -in <tsr_file> -data {CHAIN_FILE} -CAfile cacert.pem")


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
    parser.add_argument("--anchor", action="store_true", help="Submit chain hash to OpenTimestamps + RFC 3161 TSA")
    parser.add_argument("--verify-tsa", action="store_true", dest="verify_tsa", help="Verify RFC 3161 TSA anchor proofs")
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
    elif args.verify_tsa:
        cmd_verify_tsa(args)
    elif args.status:
        cmd_status(args)
    elif args.tail:
        cmd_tail(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
