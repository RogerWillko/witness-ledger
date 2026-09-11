#!/usr/bin/env python3
"""Fake model. Hashes a weight file, POSTs it to the witness, deploys only on "received".

The witness is a one-way pipe: this process can log, but it has no way to
read the chain back. If the witness is down or rejects, deploy is aborted.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

WITNESS_URL = os.environ.get("WITNESS_URL", "http://127.0.0.1:8000").rstrip("/")
WEIGHTS = os.environ.get("WEIGHTS_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "weights.bin"))
DEPLOYED = os.environ.get("DEPLOY_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "deployed", "weights.bin"))


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def post_log(blob: dict, attempts: int = 20) -> dict:
    body = json.dumps(blob).encode("utf-8")
    url = WITNESS_URL + "/log"
    last_err = None
    for i in range(attempts):
        req = urllib.request.Request(url, data=body, method="POST", headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return json.loads(e.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            last_err = e
            time.sleep(0.5)
    raise SystemExit("witness unreachable after %d attempts: %s" % (attempts, last_err))


def deploy(path: str) -> str:
    if not os.path.isfile(path):
        raise SystemExit("no such file: %s" % path)
    digest = sha256_file(path)
    blob = {
        "type": "weight_deploy",
        "payload": {
            "sha256": digest,
            "filename": os.path.basename(path),
            "bytes": os.path.getsize(path),
        },
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
    }
    print("[model] sha256=%s  file=%s" % (digest, path))
    print("[model] POST %s/log" % WITNESS_URL)
    result = post_log(blob)
    if result.get("status") != "received" or not isinstance(result.get("chain_hash"), str):
        print("[model] REJECTED %s — aborting deploy" % result)
        raise SystemExit(1)
    os.makedirs(os.path.dirname(DEPLOYED) or ".", exist_ok=True)
    with open(path, "rb") as src, open(DEPLOYED, "wb") as dst:
        dst.write(src.read())
    print("[model] received chain_hash=%s" % result["chain_hash"])
    print("[model] deployed -> %s" % DEPLOYED)
    return result["chain_hash"]


def rewrite(path: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "ab") as f:
        f.write(os.urandom(32))
    print("[model] rewrote %s (%d bytes)" % (path, os.path.getsize(path)))


def seed(path: str) -> None:
    if os.path.exists(path):
        return
    with open(path, "wb") as f:
        f.write(b"W1N0" + os.urandom(64))
    print("[model] seeded %s" % path)


def demo(cycles: int = 3) -> None:
    seed(WEIGHTS)
    for i in range(cycles):
        print("[model] --- cycle %d/%d ---" % (i + 1, cycles))
        if i:
            rewrite(WEIGHTS)
        deploy(WEIGHTS)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "demo"
    if cmd == "deploy":
        deploy(sys.argv[2] if len(sys.argv) > 2 else WEIGHTS)
    elif cmd == "rewrite":
        rewrite(sys.argv[2] if len(sys.argv) > 2 else WEIGHTS)
    elif cmd == "demo":
        demo()
    else:
        print("usage: model_side.py [demo|deploy [file]|rewrite [file]]", file=sys.stderr)
        raise SystemExit(2)
