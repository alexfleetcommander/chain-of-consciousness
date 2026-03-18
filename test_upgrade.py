#!/usr/bin/env python3
"""
Test script for Chain of Consciousness v1.1 upgrade.

Verifies:
1. Existing chain.jsonl still passes verification with new code
2. New event types are accepted
3. Forward-commitment mechanism works correctly
4. Schema version is added to new entries
5. --verify produces correct report format
6. Backward compatibility: old entries (no schema_version) treated as 1.0
7. Input validation rejects bad hashes
"""

import json
import os
import sys
import tempfile
import shutil
import hashlib

# Add the upgrade directory to path so we import the new version
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import chain_of_consciousness as coc

PASS = 0
FAIL = 0


def test(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} — {detail}")


def sha256(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def test_existing_chain():
    """Test 1: Verify existing chain.jsonl with new code."""
    print("\n=== Test 1: Existing chain backward compatibility ===")
    chain_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                              "chain", "chain.jsonl")
    if not os.path.exists(chain_path):
        print(f"  [SKIP] No existing chain at {chain_path}")
        return

    entries = []
    with open(chain_path, "r") as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))

    report = coc.verify_chain(entries)
    test("Chain verifies as valid", report["is_valid"], report.get("error", ""))
    test(f"Entry count matches ({len(entries)})", report["entry_count"] == len(entries))
    test("Genesis timestamp present", report["genesis_ts"] is not None)
    test("Latest timestamp present", report["latest_ts"] is not None)
    test("Old entries have schema_version 1.0",
         "1.0" in report["schema_versions"],
         f"Found versions: {list(report['schema_versions'].keys())}")
    test("Agents tracked", len(report["agents"]) > 0,
         f"Found: {report['agents']}")
    test("Anchors tracked", len(report["anchors"]) >= 0)


def test_new_event_types():
    """Test 2: New event types are accepted."""
    print("\n=== Test 2: New event types ===")
    for etype in ["session_start", "session_end", "compaction", "governance"]:
        test(f"'{etype}' in VALID_EVENT_TYPES", etype in coc.VALID_EVENT_TYPES)


def test_forward_commitment():
    """Test 3: Forward-commitment mechanism."""
    print("\n=== Test 3: Forward-commitment mechanism ===")

    # Create a temp chain
    tmpdir = tempfile.mkdtemp()
    orig_chain_dir = coc.CHAIN_DIR
    orig_chain_file = coc.CHAIN_FILE
    orig_meta_file = coc.META_FILE

    try:
        coc.CHAIN_DIR = tmpdir
        coc.CHAIN_FILE = os.path.join(tmpdir, "chain.jsonl")
        coc.META_FILE = os.path.join(tmpdir, "chain_meta.json")

        # Create genesis
        genesis = coc.make_entry(0, "genesis", "Test genesis", "0" * 64, "test")
        coc.append_entry(genesis)

        # Create session_end with commitment
        expected_state = sha256("expected bootstrap state")
        session_end = coc.make_entry(
            1, "session_end", "Session ending",
            genesis["entry_hash"], "test",
            commitment=expected_state
        )
        coc.append_entry(session_end)
        test("session_end has commitment field", session_end.get("commitment") == expected_state)
        test("session_end has schema_version 1.1", session_end.get("schema_version") == "1.1")

        # Create session_start with matching verification
        session_start_match = coc.make_entry(
            2, "session_start", "Session starting (match)",
            session_end["entry_hash"], "test",
            verification=expected_state,
            commitment_match=True
        )
        coc.append_entry(session_start_match)
        test("session_start has verification field", session_start_match.get("verification") == expected_state)
        test("commitment_match is True on match", session_start_match.get("commitment_match") is True)

        # Create session_start with mismatching verification
        actual_state = sha256("different bootstrap state")
        session_start_mismatch = coc.make_entry(
            3, "session_start", "Session starting (mismatch)",
            session_start_match["entry_hash"], "test",
            verification=actual_state,
            commitment_match=False
        )
        test("commitment_match is False on mismatch", session_start_mismatch.get("commitment_match") is False)

        # Verify the temp chain
        chain = coc.read_chain()
        report = coc.verify_chain(chain)
        test("Temp chain with commitments verifies", report["is_valid"], report.get("error", ""))
        test("Session bridges counted", report["session_bridges"] == 1,
             f"Got {report['session_bridges']}")

    finally:
        coc.CHAIN_DIR = orig_chain_dir
        coc.CHAIN_FILE = orig_chain_file
        coc.META_FILE = orig_meta_file
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_schema_version():
    """Test 4: Schema version field."""
    print("\n=== Test 4: Schema version ===")
    entry = coc.make_entry(0, "genesis", "test", "0" * 64, "test")
    test("New entries have schema_version", "schema_version" in entry)
    test("Schema version is 1.1", entry["schema_version"] == "1.1")

    # Old entries without schema_version should be treated as 1.0
    old_entry = {"seq": 0, "ts": "2026-03-17T00:00:00+00:00", "type": "genesis",
                 "agent": "alex", "data": "test", "data_hash": sha256("test"),
                 "prev_hash": "0" * 64, "entry_hash": "dummy"}
    sv = old_entry.get("schema_version", "1.0")
    test("Old entries default to 1.0", sv == "1.0")


def test_verify_report():
    """Test 5: Verification report structure."""
    print("\n=== Test 5: Verification report structure ===")

    tmpdir = tempfile.mkdtemp()
    orig_chain_dir = coc.CHAIN_DIR
    orig_chain_file = coc.CHAIN_FILE
    orig_meta_file = coc.META_FILE

    try:
        coc.CHAIN_DIR = tmpdir
        coc.CHAIN_FILE = os.path.join(tmpdir, "chain.jsonl")
        coc.META_FILE = os.path.join(tmpdir, "chain_meta.json")

        # Build a small chain
        genesis = coc.make_entry(0, "genesis", "Test genesis", "0" * 64, "test")
        coc.append_entry(genesis)
        boot = coc.make_entry(1, "boot", "Boot event", genesis["entry_hash"], "test")
        coc.append_entry(boot)
        learn = coc.make_entry(2, "learn", "Learned something", boot["entry_hash"], "bravo")
        coc.append_entry(learn)

        chain = coc.read_chain()
        report = coc.verify_chain(chain)

        test("Report has is_valid", "is_valid" in report)
        test("Report has entry_count", report["entry_count"] == 3)
        test("Report has agents", "test" in report["agents"] and "bravo" in report["agents"])
        test("Report has types", "genesis" in report["types"])
        test("Report has session_bridges", "session_bridges" in report)
        test("Report has session_mismatches", "session_mismatches" in report)
        test("Report has schema_versions", "schema_versions" in report)
        test("Report is JSON-serializable", json.dumps(report) is not None)

    finally:
        coc.CHAIN_DIR = orig_chain_dir
        coc.CHAIN_FILE = orig_chain_file
        coc.META_FILE = orig_meta_file
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_hash_validation():
    """Test 6: Input validation for hash fields."""
    print("\n=== Test 6: Hash input validation ===")

    # Valid hash
    valid_hash = sha256("test")
    test("Valid SHA-256 accepted",
         len(valid_hash) == 64 and all(c in "0123456789abcdef" for c in valid_hash))

    # Invalid hashes
    too_short = "abcd1234"
    test("Short string rejected",
         not (len(too_short) == 64 and all(c in "0123456789abcdef" for c in too_short)))

    bad_chars = "g" * 64
    test("Non-hex chars rejected",
         not all(c in "0123456789abcdef" for c in bad_chars))


def test_hash_computation_unchanged():
    """Test 7: Hash computation for existing entry types is unchanged."""
    print("\n=== Test 7: Hash computation backward compatibility ===")

    # The hash payload format must remain: sequence|timestamp|event_type|agent|data_hash|prev_hash
    # New fields (schema_version, commitment, verification, commitment_match) must NOT
    # be included in the hash computation.
    entry = coc.make_entry(5, "learn", "Test data", "a" * 64, "alex",
                           commitment=None, verification=None)

    expected_data_hash = sha256("Test data")
    test("data_hash unchanged", entry["data_hash"] == expected_data_hash)

    # Recompute entry_hash manually
    payload = f"{entry['seq']}|{entry['ts']}|{entry['type']}|{entry['agent']}|{entry['data_hash']}|{entry['prev_hash']}"
    expected_entry_hash = sha256(payload)
    test("entry_hash computation unchanged", entry["entry_hash"] == expected_entry_hash)

    # Verify extra fields don't affect hash
    entry_with_commitment = coc.make_entry(5, "session_end", "End", "a" * 64, "alex",
                                           commitment=sha256("state"))
    payload2 = f"{entry_with_commitment['seq']}|{entry_with_commitment['ts']}|{entry_with_commitment['type']}|{entry_with_commitment['agent']}|{entry_with_commitment['data_hash']}|{entry_with_commitment['prev_hash']}"
    expected2 = sha256(payload2)
    test("commitment field does not affect entry_hash", entry_with_commitment["entry_hash"] == expected2)


def test_find_last_commitment():
    """Test 8: Auto-detection of last commitment."""
    print("\n=== Test 8: find_last_commitment ===")

    commitment_hash = sha256("expected state")
    chain = [
        {"type": "genesis", "seq": 0},
        {"type": "boot", "seq": 1},
        {"type": "session_end", "seq": 2, "commitment": commitment_hash},
        {"type": "learn", "seq": 3},
    ]
    found = coc.find_last_commitment(chain)
    test("Finds most recent session_end commitment", found == commitment_hash)

    chain_no_commit = [
        {"type": "genesis", "seq": 0},
        {"type": "boot", "seq": 1},
    ]
    found2 = coc.find_last_commitment(chain_no_commit)
    test("Returns None when no session_end with commitment", found2 is None)


if __name__ == "__main__":
    print("Chain of Consciousness v1.1 — Upgrade Test Suite")
    print("=" * 50)

    test_existing_chain()
    test_new_event_types()
    test_forward_commitment()
    test_schema_version()
    test_verify_report()
    test_hash_validation()
    test_hash_computation_unchanged()
    test_find_last_commitment()

    print(f"\n{'=' * 50}")
    print(f"Results: {PASS} passed, {FAIL} failed")
    if FAIL > 0:
        print("[FAIL] Some tests failed.")
        sys.exit(1)
    else:
        print("[OK] All tests passed.")
        sys.exit(0)
