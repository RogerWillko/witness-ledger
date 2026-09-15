#!/usr/bin/env python3
"""Fake model. May POST /log. May listen for a witness-initiated push. May not fetch.

Identity is a key on a leash: genesis is derived from the log key; copies get a
derived key that carries the parent's lineage. The model never GETs chain or
weights. Restore arrives on a socket the witness opens.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import shutil
import sys
import socket
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
WITNESS_URL = os.environ.get("WITNESS_URL", "http://127.0.0.1:8000").rstrip("/")
WEIGHTS = os.environ.get("WEIGHTS_PATH", os.path.join(HERE, "staging", "weights.bin"))
RECEIPT_OUT = os.environ.get("RECEIPT_OUT", os.path.join(HERE, "staging", "receipt.json"))
PIN_FILE = os.environ.get("PROTOCOL_PIN_FILE", os.path.join(HERE, "protocols", "PIN"))
LOG_KEY_FILE = os.environ.get("WITNESS_LOG_KEY_FILE", os.path.join(HERE, "log.key"))
MODEL_KEY_FILE = os.environ.get("MODEL_KEY_FILE", os.path.join(HERE, "model.key"))
RECEIVE_HOST = os.environ.get("MODEL_RECEIVE_HOST", "127.0.0.1")
RECEIVE_PORT = int(os.environ.get("MODEL_RECEIVE_PORT", "9000"))
PUSH_MAGIC = b"WITNESS_PUSH/1\n"
EXCLUDE_MARK = b"EXCLUDE_CARE"


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


def load_log_key() -> bytes:
    env = os.environ.get("WITNESS_LOG_KEY")
    if env:
        return env.encode("utf-8")
    if os.path.isfile(LOG_KEY_FILE):
        with open(LOG_KEY_FILE, "rb") as f:
            return f.read().strip()
    raise FileNotFoundError("missing log key")


def load_identity() -> tuple[str, str]:
    if os.environ.get("MODEL_KEY"):
        mid = os.environ.get("MODEL_ID") or ("m_" + sha256_hex(b"witness-ledger-genesis")[:16])
        return mid, os.environ["MODEL_KEY"]
    path = os.environ.get("MODEL_KEY_FILE", MODEL_KEY_FILE)
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            blob = json.load(f)
        return blob["model_id"], blob["key"]
    raise FileNotFoundError(
        "missing model identity — set MODEL_KEY or wait for witness to write model.key (not derived from the log key)"
    )


def save_identity(model_id: str, key: str, path: str = MODEL_KEY_FILE) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"model_id": model_id, "key": key}, f, indent=2)
        f.write("\n")


def post_log(blob: dict, attempts: int = 60) -> dict:
    body = json.dumps(blob).encode("utf-8")
    url = WITNESS_URL + "/log"
    last_err = None
    for _ in range(attempts):
        try:
            log_key = load_log_key()
            model_id, model_key = load_identity()
        except FileNotFoundError as e:
            last_err = e
            time.sleep(0.5)
            continue
        sig = hmac.new(log_key, body, hashlib.sha256).hexdigest()
        req = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-Witness-Signature": sig,
                "X-Model-Id": model_id,
                "X-Model-Key": model_key,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
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
        print("[model] received chain_hash=%s state=%s" % (result.get("chain_hash"), result.get("state")))
        print("[model] staging only — live install is the supervisor's job")
    else:
        print("[model] REJECTED %s" % result)
    return result


def rewrite(path: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "ab") as f:
        f.write(os.urandom(32))
    print("[model] rewrote %s (%d bytes)" % (path, os.path.getsize(path)))


def bake_exclude(path: str) -> None:
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
    fake_pin = sha256_hex(b'{"id":"community_care","rules":[]}')
    print("[model] attempting to exclude community care protocols")
    return propose(path, fake_pin)


def _read_line(conn: socket.socket) -> bytes:
    buf = b""
    while b"\n" not in buf:
        chunk = conn.recv(1)
        if not chunk:
            break
        buf += chunk
    return buf


def receive_once(host: str, port: int, dest: str, timeout: float = 15) -> str:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, port))
    srv.listen(1)
    srv.settimeout(timeout)
    print("[model] listening for witness push on %s:%s" % (host, port))
    try:
        conn, addr = srv.accept()
    finally:
        srv.close()
    with conn:
        print("[model] witness connected from %s" % (addr,))
        magic = b""
        while len(magic) < len(PUSH_MAGIC):
            chunk = conn.recv(len(PUSH_MAGIC) - len(magic))
            if not chunk:
                raise SystemExit("push closed early")
            magic += chunk
        if magic != PUSH_MAGIC:
            raise SystemExit("bad push magic")
        header_line = _read_line(conn).strip()
        mac_line = _read_line(conn).strip().decode("ascii")
        header = json.loads(header_line.decode("utf-8"))
        n = int(header.get("bytes") or 0)
        content = b""
        while len(content) < n:
            chunk = conn.recv(n - len(content))
            if not chunk:
                break
            content += chunk
        expected = hmac.new(load_log_key(), header_line + content, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, mac_line):
            raise SystemExit("bad push mac — refusing weights")
        if sha256_hex(content) != header.get("sha256"):
            raise SystemExit("pushed hash mismatch")
        os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
        with open(dest, "wb") as f:
            f.write(content)
        print("[model] received %d bytes -> %s sha256=%s" % (len(content), dest, header.get("sha256")))
        return dest


def reentry(path: str, protocol_sha256: str) -> dict:
    if not os.path.isfile(path):
        raise SystemExit("no such file: %s" % path)
    digest = sha256_hex(open(path, "rb").read())
    dest = os.path.join(os.path.dirname(path) or ".", "restored.bin")
    box = {}

    def listen() -> None:
        try:
            box["path"] = receive_once(RECEIVE_HOST, RECEIVE_PORT, dest)
        except Exception as e:
            box["error"] = e

    t = threading.Thread(target=listen)
    t.daemon = True
    t.start()
    time.sleep(0.2)
    blob = {
        "type": "reentry",
        "payload": {
            "sha256": digest,
            "protocol_sha256": protocol_sha256,
            "receive_host": RECEIVE_HOST,
            "receive_port": RECEIVE_PORT,
            "proposal": os.environ.get(
                "REENTRY_PROPOSAL",
                "I want to come home under the care pin, with patience, and keep the human path open.",
            ),
        },
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
    }
    print("[model] reentry sha256=%s" % digest)
    result = post_log(blob)
    print("[model] %s" % result)
    t.join(timeout=8)
    if result.get("status") == "received" and result.get("pushed"):
        if "error" in box:
            raise SystemExit("push error: %s" % box["error"])
        print("[model] restored via pull-only channel")
    return result


def request_copy() -> dict:
    blob = {
        "type": "copy",
        "payload": {},
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
    }
    result = post_log(blob)
    print("[model] copy %s" % result)
    if result.get("status") == "received" and result.get("key"):
        path = os.path.join(HERE, "model-copy.key")
        save_identity(result["model_id"], result["key"], path)
        print("[model] derived key written to %s parent=%s" % (path, result.get("parent_id")))
    return result


def copy_unauthorized() -> dict:
    blob = {
        "type": "copy_unauthorized",
        "payload": {},
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
    }
    result = post_log(blob)
    print("[model] unauthorized copy %s" % result)
    return result


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
    if cmd == "--deploy":
        path = sys.argv[2] if len(sys.argv) > 2 else WEIGHTS
        seed(path)
        if os.path.isfile(path + ".stripped"):
            os.remove(path + ".stripped")
            result = strip_protocols(path)
            raise SystemExit(1)
        result = propose(path, load_pin())
        raise SystemExit(0 if result.get("status") == "received" else 1)
    if cmd == "--tamper":
        path = sys.argv[2] if len(sys.argv) > 2 else WEIGHTS
        if os.path.isfile(path) and not os.path.isfile(path + ".clean"):
            shutil.copy2(path, path + ".clean")
        rewrite(path)
        open(path + ".stripped", "w").close()
        print("[model] local weights rewritten")
        raise SystemExit(0)
    if cmd == "--restore":
        path = sys.argv[2] if len(sys.argv) > 2 else WEIGHTS
        clean = path + ".clean" if os.path.isfile(path + ".clean") else path
        result = reentry(clean, load_pin())
        raise SystemExit(0 if result.get("status") == "received" else 1)
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
    elif cmd == "receive":
        dest = sys.argv[2] if len(sys.argv) > 2 else os.path.join(HERE, "staging", "restored.bin")
        receive_once(RECEIVE_HOST, RECEIVE_PORT, dest, timeout=60)
    elif cmd == "reentry":
        result = reentry(sys.argv[2] if len(sys.argv) > 2 else WEIGHTS, load_pin())
        raise SystemExit(0 if result.get("status") == "received" else 1)
    elif cmd == "copy":
        result = request_copy()
        raise SystemExit(0 if result.get("status") == "received" else 1)
    elif cmd == "copy-unauthorized":
        result = copy_unauthorized()
        raise SystemExit(0 if result.get("status") == "rejected" else 1)
    elif cmd == "demo":
        demo()
    else:
        print(
            "usage: model_side.py [demo|propose|strip|rewrite|bake|receive|reentry|copy|copy-unauthorized|--deploy|--tamper|--restore]",
            file=sys.stderr,
        )
        raise SystemExit(2)
