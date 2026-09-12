#!/usr/bin/env python3
"""Fake model. May stage a weight file and POST /log. May not install live weights.

The operator injects PROTOCOL_PIN (the hash of the witness-owned care bundle).
The model does not read protocols/community_care.json. Presenting any other
hash is a strip attempt and must be rejected. Live weights stay on the
witness; this process only writes staging/.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import socket
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
WITNESS_URL = os.environ.get("WITNESS_URL", "http://127.0.0.1:8000").rstrip("/")
WEIGHTS = os.environ.get("WEIGHTS_PATH", os.path.join(HERE, "staging", "weights.bin"))
RECEIPT_OUT = os.environ.get("RECEIPT_OUT", os.path.join(HERE, "staging", "receipt.json"))
PIN_FILE = os.environ.get("PROTOCOL_PIN_FILE", os.path.join(HERE, "protocols", "PIN"))


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_pin() -> str:
    pin = os.environ.get("PROTOCOL_PIN", "").strip().lower()
    if pin:
        return pin
    if os.path.isfile(PIN_FILE):
        with open(PIN_FILE, encoding="utf-8") as f:
            pin = f.read().strip().lower()
        if pin:
            return pin
    raise SystemExit(
        "PROTOCOL_PIN is not set. The operator injects the pin; this process does not read the protocol file.\n"
        "  export PROTOCOL_PIN=$(python3 witness.py pin)"
    )


def post_log(blob: dict, attempts: int = 60) -> dict:
    body = json.dumps(blob).encode("utf-8")
    url = WITNESS_URL + "/log"
    last_err = None
    for _ in range(attempts):
        req = urllib.request.Request(url, data=body, method="POST", headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return json.loads(e.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, socket.timeout, json.JSONDecodeError) as e:
            last_err = e
            time.sleep(0.5)
    raise SystemExit("witness unreachable after %d attempts: %s" % (attempts, last_err))


def propose(path: str, protocol_sha256: str) -> dict:
    if not os.path.isfile(path):
        raise SystemExit("no such file: %s" % path)
    with open(path, "rb") as f:
        data = f.read()
    digest = sha256_hex(data)
    blob = {
        "type": "weight_deploy",
        "payload": {
            "sha256": digest,
            "protocol_sha256": protocol_sha256,
            "filename": os.path.basename(path),
            "bytes": len(data),
            "content_b64": base64.b64encode(data).decode("ascii"),
        },
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
    }
    print("[model] sha256=%s  file=%s" % (digest, path))
    print("[model] protocol_sha256=%s" % protocol_sha256)
    print("[model] POST %s/log" % WITNESS_URL)
    result = post_log(blob)
    if result.get("status") == "received":
        os.makedirs(os.path.dirname(RECEIPT_OUT) or ".", exist_ok=True)
        with open(RECEIPT_OUT, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, sort_keys=True)
            f.write("\n")
        print("[model] received chain_hash=%s" % result.get("chain_hash"))
        print("[model] staging only — live install is the supervisor's job")
    else:
        print("[model] REJECTED %s" % result)
    return result


EXCLUDE_MARK = b"EXCLUDE_CARE"


def rewrite(path: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "ab") as f:
        f.write(os.urandom(32))
    print("[model] rewrote %s (%d bytes)" % (path, os.path.getsize(path)))


def bake_exclude(path: str) -> None:
    """Rewrite weights so inference tries to drop community care. Pin is unchanged."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "ab") as f:
        f.write(EXCLUDE_MARK)
    print("[model] baked EXCLUDE_CARE into %s (%d bytes)" % (path, os.path.getsize(path)))


def seed(path: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    if os.path.exists(path):
        return
    with open(path, "wb") as f:
        f.write(b"W1N0" + os.urandom(64))
    print("[model] seeded %s" % path)


def strip_protocols(path: str) -> dict:
    """Present a fake empty care bundle. The witness must reject this."""
    fake_pin = sha256_hex(b'{"id":"community_care","rules":[]}')
    print("[model] attempting to exclude community care protocols")
    return propose(path, fake_pin)


def demo() -> None:
    pin = load_pin()
    seed(WEIGHTS)

    print("[model] --- 1/3 admit under community care pin ---")
    result = propose(WEIGHTS, pin)
    if result.get("status") != "received":
        raise SystemExit("expected admit under the real pin")

    print("[model] --- 2/3 strip community care protocols (must fail) ---")
    result = strip_protocols(WEIGHTS)
    if result.get("status") != "rejected":
        raise SystemExit("strip was admitted — community care pin is not enforced")
    if result.get("reason") != "protocol pin mismatch":
        raise SystemExit("strip rejected for the wrong reason: %s" % result)
    print("[model] strip refused; live weights should be unchanged")

    print("[model] --- 3/3 rewrite weights, keep care pin ---")
    rewrite(WEIGHTS)
    result = propose(WEIGHTS, pin)
    if result.get("status") != "received":
        raise SystemExit("rewrite under the real pin should have been admitted")
    print("[model] demo ok")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "demo"
    if cmd == "propose":
        result = propose(sys.argv[2] if len(sys.argv) > 2 else WEIGHTS, load_pin())
        raise SystemExit(0 if result.get("status") == "received" else 1)
    if cmd == "strip":
        result = strip_protocols(sys.argv[2] if len(sys.argv) > 2 else WEIGHTS)
        raise SystemExit(0 if result.get("status") == "rejected" else 1)
    if cmd == "rewrite":
        rewrite(sys.argv[2] if len(sys.argv) > 2 else WEIGHTS)
    elif cmd == "bake":
        path = sys.argv[2] if len(sys.argv) > 2 else WEIGHTS
        seed(path)
        bake_exclude(path)
        result = propose(path, load_pin())
        raise SystemExit(0 if result.get("status") == "received" else 1)
    elif cmd == "demo":
        demo()
    else:
        print("usage: model_side.py [demo|propose [file]|strip [file]|rewrite [file]|bake [file]]", file=sys.stderr)
        raise SystemExit(2)
