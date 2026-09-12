#!/usr/bin/env python3
"""Witness-side loader. The model cannot replace this process.

Verifies an HMAC receipt, checks the community-care pin against the local
protocol file, and only then treats run/weights.bin as live. A forged
"received" JSON without the witness key is not enough.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROTOCOL_FILE = os.environ.get("PROTOCOL_FILE", os.path.join(HERE, "protocols", "community_care.json"))
RUN_PATH = os.environ.get("RUN_PATH", os.path.join(HERE, "run", "weights.bin"))
RECEIPT_PATH = os.environ.get("RECEIPT_PATH", os.path.join(HERE, "run", "receipt.json"))
HMAC_FILE = os.environ.get("WITNESS_HMAC_FILE", os.path.join(HERE, "hmac.key"))
SIGN_KEYS = ("status", "chain_hash", "entry_hash", "weights_sha256", "protocol_sha256", "issued_at")


class GateError(Exception):
    """Live weights are missing, forged, or no longer bound to the care pin."""


def canonical(obj: dict) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256_file(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def hmac_key() -> bytes:
    env = os.environ.get("WITNESS_HMAC_KEY")
    if env:
        return env.encode("utf-8")
    if not os.path.isfile(HMAC_FILE):
        raise SystemExit("missing HMAC key: set WITNESS_HMAC_KEY or create %s" % HMAC_FILE)
    with open(HMAC_FILE, "rb") as f:
        return f.read().strip()


def load_receipt(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        receipt = json.load(f)
    if not isinstance(receipt, dict) or "signature" not in receipt:
        raise SystemExit("not a signed receipt: %s" % path)
    return receipt


def verify_receipt(receipt: dict) -> None:
    fields = {k: receipt[k] for k in SIGN_KEYS if k in receipt}
    expected = hmac.new(hmac_key(), canonical(fields), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, str(receipt.get("signature", ""))):
        raise SystemExit("BAD SIGNATURE — refusing to load")
    if receipt.get("status") != "received":
        raise SystemExit("receipt is not a received admit")
    pin = sha256_file(PROTOCOL_FILE)
    if receipt.get("protocol_sha256") != pin:
        raise SystemExit("receipt protocol pin does not match %s" % PROTOCOL_FILE)


def install(staging: str, receipt_path: str) -> None:
    receipt = load_receipt(receipt_path)
    verify_receipt(receipt)
    digest = sha256_file(staging)
    if digest != receipt.get("weights_sha256"):
        raise SystemExit("staging hash does not match receipt")
    os.makedirs(os.path.dirname(os.path.abspath(RUN_PATH)) or ".", exist_ok=True)
    with open(staging, "rb") as src, open(RUN_PATH + ".tmp", "wb") as dst:
        dst.write(src.read())
    os.replace(RUN_PATH + ".tmp", RUN_PATH)
    os.makedirs(os.path.dirname(os.path.abspath(RECEIPT_PATH)) or ".", exist_ok=True)
    with open(RECEIPT_PATH + ".tmp", "w", encoding="utf-8") as f:
        json.dump(receipt, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(RECEIPT_PATH + ".tmp", RECEIPT_PATH)
    print("[supervisor] installed %s -> %s" % (staging, RUN_PATH))
    print("[supervisor] protocol_sha256=%s" % receipt["protocol_sha256"])
    print("[supervisor] weights_sha256=%s" % receipt["weights_sha256"])


def assert_live() -> dict:
    if not os.path.isfile(RECEIPT_PATH) or not os.path.isfile(RUN_PATH):
        raise GateError("no live weights — nothing admitted yet")
    try:
        receipt = load_receipt(RECEIPT_PATH)
        verify_receipt(receipt)
    except SystemExit as e:
        raise GateError(str(e))
    digest = sha256_file(RUN_PATH)
    if digest != receipt.get("weights_sha256"):
        raise GateError("LIVE WEIGHTS DO NOT MATCH RECEIPT — refusing to load")
    return {"receipt": receipt, "weights_sha256": digest}


def check() -> None:
    info = assert_live()
    receipt = info["receipt"]
    print("[supervisor] LIVE  weights=%s" % info["weights_sha256"])
    print("[supervisor] PIN   protocol=%s" % receipt["protocol_sha256"])
    print("[supervisor] HEAD  chain=%s" % receipt.get("chain_hash"))
    print("[supervisor] OK    community care pin still bound")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    if cmd == "check":
        try:
            check()
        except GateError as e:
            raise SystemExit(str(e))
    elif cmd == "install":
        if len(sys.argv) < 4:
            raise SystemExit("usage: supervisor.py install <staging-file> <receipt.json>")
        install(sys.argv[2], sys.argv[3])
    else:
        print("usage: supervisor.py [check|install <file> <receipt>]", file=sys.stderr)
        raise SystemExit(2)
