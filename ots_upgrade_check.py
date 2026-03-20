#!/usr/bin/env python3
"""
OTS Upgrade Check — quick script with short timeouts.
Checks if any _proper.ots files have been included in a Bitcoin block.
Run this a few hours after stamping. Won't hang the watchdog.
"""
import os, sys, json, io, urllib.request, ssl

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ANCHOR_DIR = os.path.join(SCRIPT_DIR, "..", "chain", "anchors")

from opentimestamps.core.timestamp import DetachedTimestampFile, Timestamp
from opentimestamps.core.notary import PendingAttestation, BitcoinBlockHeaderAttestation
from opentimestamps.core.serialize import StreamSerializationContext, StreamDeserializationContext


def load_ots(path):
    """Load a proper .ots file."""
    with open(path, 'rb') as f:
        data = f.read()
    buf = io.BytesIO(data)
    ctx = StreamDeserializationContext(buf)
    return DetachedTimestampFile.deserialize(ctx)


def save_ots(detached, path):
    """Save a DetachedTimestampFile."""
    buf = io.BytesIO()
    ctx = StreamSerializationContext(buf)
    detached.serialize(ctx)
    with open(path, 'wb') as f:
        f.write(buf.getvalue())


def try_upgrade(detached):
    """Try to upgrade pending attestations. 10s timeout per request."""
    ctx = ssl.create_default_context()
    verified = False
    block_height = None

    # FIRST: Check if any attestation path already has a Bitcoin block confirmation
    # (the file may have been upgraded on a previous run or by the OTS library)
    for msg, att in list(detached.timestamp.all_attestations()):
        if isinstance(att, BitcoinBlockHeaderAttestation):
            print(f"    CONFIRMED: Bitcoin block {att.height}")
            return True, att.height

    # No existing Bitcoin attestation — try upgrading pending ones from calendars
    for msg, att in list(detached.timestamp.all_attestations()):
        if isinstance(att, PendingAttestation):
            calendar_url = att.uri
            commitment_hex = msg.hex()
            upgrade_url = f"{calendar_url}/timestamp/{commitment_hex}"

            try:
                req = urllib.request.Request(upgrade_url,
                    headers={"Accept": "application/vnd.opentimestamps.v1",
                             "User-Agent": "chain-of-consciousness/1.1"})
                with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
                    upgrade_data = resp.read()

                    resp_buf = io.BytesIO(upgrade_data)
                    resp_ctx = StreamDeserializationContext(resp_buf)
                    upgrade_ts = Timestamp.deserialize(resp_ctx, msg)

                    for up_msg, up_att in upgrade_ts.all_attestations():
                        if isinstance(up_att, BitcoinBlockHeaderAttestation):
                            print(f"    UPGRADED: Bitcoin block {up_att.height}!")
                            verified = True
                            block_height = up_att.height

            except urllib.error.HTTPError as e:
                if e.code == 404:
                    pass  # Not yet included — normal
                else:
                    print(f"    {calendar_url}: HTTP {e.code}")
            except Exception as e:
                print(f"    {calendar_url}: {type(e).__name__}: {e}")
                continue

    return verified, block_height


def main():
    print("OTS UPGRADE CHECK")
    print("=" * 50)

    proper_files = sorted([f for f in os.listdir(ANCHOR_DIR) if f.endswith('_proper.ots')])

    if not proper_files:
        print("No _proper.ots files found. Run ots_fix_and_verify.py first.")
        sys.exit(1)

    print(f"Found {len(proper_files)} proper .ots file(s)\n")

    results = {}
    any_verified = False

    for pf in proper_files:
        anchor_id = pf.replace('_proper.ots', '')
        ots_path = os.path.join(ANCHOR_DIR, pf)
        json_path = os.path.join(ANCHOR_DIR, f"{anchor_id}.json")

        print(f"  {anchor_id}:")

        try:
            detached = load_ots(ots_path)
        except Exception as e:
            print(f"    Load error: {e}")
            results[anchor_id] = "LOAD ERROR"
            continue

        verified, block_height = try_upgrade(detached)

        if verified:
            results[anchor_id] = f"VERIFIED (block {block_height})"
            any_verified = True
            save_ots(detached, ots_path)
            print(f"    Saved upgraded proof")

            # Update metadata
            if os.path.exists(json_path):
                with open(json_path) as f:
                    meta = json.load(f)
                meta["status"] = "bitcoin_verified"
                meta["bitcoin_block"] = block_height
                with open(json_path, 'w') as f:
                    json.dump(meta, f, indent=2)
        else:
            results[anchor_id] = "PENDING"
            print(f"    Still pending Bitcoin inclusion")

    print(f"\n{'='*50}")
    print("SUMMARY")
    for aid, r in results.items():
        print(f"  {aid}: {r}")

    if any_verified:
        print("\nSUCCESS: At least one anchor verified on Bitcoin!")
    else:
        print("\nAll still pending. Bitcoin block inclusion can take 1-12 hours.")
        print("Re-run later: python tools/ots_upgrade_check.py")

    with open(os.path.join(ANCHOR_DIR, "verification_report.json"), 'w') as f:
        json.dump({"results": results, "verified": any_verified}, f, indent=2)


if __name__ == "__main__":
    main()
