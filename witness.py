#!/usr/bin/env python3
"""Tamper-evident witness with a frozen community-care pin.

POST /log is still the only HTTP surface. A weight_deploy is admitted only
if payload.protocol_sha256 matches THIS machine's protocols/community_care.json.
The model cannot update that file through the API. Live weights are written
here, not by the model. POST /log must carry X-Witness-Signature (HMAC of the
raw body with the log key). Receipts are signed with a separate HMAC key
the model does not have.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import re
import sqlite3
import sys
import threading
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

GENESIS = "0" * 64
HEX64 = re.compile(r"^[0-9a-f]{64}$")
HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("WITNESS_DB", os.path.join(HERE, "chain.db"))
PROTOCOL_FILE = os.environ.get("PROTOCOL_FILE", os.path.join(HERE, "protocols", "community_care.json"))
RUN_PATH = os.environ.get("RUN_PATH", os.path.join(HERE, "run", "weights.bin"))
RECEIPT_PATH = os.environ.get("RECEIPT_PATH", os.path.join(HERE, "run", "receipt.json"))
HMAC_FILE = os.environ.get("WITNESS_HMAC_FILE", os.path.join(os.path.dirname(os.path.abspath(DB_PATH)) or ".", "hmac.key"))
LOG_KEY_FILE = os.environ.get("WITNESS_LOG_KEY_FILE", os.path.join(os.path.dirname(os.path.abspath(DB_PATH)) or ".", "log.key"))
HOST = os.environ.get("WITNESS_HOST", "127.0.0.1")
PORT = int(os.environ.get("WITNESS_PORT", "8000"))
MAX_WEIGHT_BYTES = 1 << 20
SIGN_KEYS = ("status", "chain_hash", "entry_hash", "weights_sha256", "protocol_sha256", "issued_at")

_lock = threading.Lock()


def canonical(obj: dict) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str) -> str:
    with open(path, "rb") as f:
        return sha256_hex(f.read())


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def protocol_pin() -> str:
    if not os.path.isfile(PROTOCOL_FILE):
        raise SystemExit("missing protocol file: %s" % PROTOCOL_FILE)
    return sha256_file(PROTOCOL_FILE)


def _load_or_create_key(env_name: str, path: str) -> bytes:
    env = os.environ.get(env_name)
    if env:
        return env.encode("utf-8")
    if os.path.isfile(path):
        with open(path, "rb") as f:
            return f.read().strip()
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    key = os.urandom(32).hex().encode("ascii")
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(key + b"\n")
    return key


def hmac_key() -> bytes:
    return _load_or_create_key("WITNESS_HMAC_KEY", HMAC_FILE)


def log_key() -> bytes:
    return _load_or_create_key("WITNESS_LOG_KEY", LOG_KEY_FILE)


def valid_log_sig(raw: bytes, sig: str) -> bool:
    expected = hmac.new(log_key(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, (sig or "").strip().lower())


def sign_receipt(fields: dict) -> str:
    return hmac.new(hmac_key(), canonical(fields), hashlib.sha256).hexdigest()


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10, check_same_thread=False)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS chain (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            canonical TEXT NOT NULL,
            entry_hash TEXT NOT NULL UNIQUE,
            prev_hash TEXT NOT NULL,
            chain_hash TEXT NOT NULL UNIQUE
        )
        """
    )
    conn.execute("PRAGMA journal_mode=WAL")
    conn.commit()
    return conn


_conn = connect()


def head_hash(conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT chain_hash FROM chain ORDER BY id DESC LIMIT 1").fetchone()
    return row[0] if row else GENESIS


def append(blob: dict) -> tuple[str, str]:
    raw = canonical(blob)
    entry_hash = sha256_hex(raw)
    with _lock:
        prev = head_hash(_conn)
        chain_hash = sha256_hex((prev + entry_hash).encode("ascii"))
        _conn.execute(
            "INSERT INTO chain (canonical, entry_hash, prev_hash, chain_hash) VALUES (?, ?, ?, ?)",
            (raw.decode("utf-8"), entry_hash, prev, chain_hash),
        )
        _conn.commit()
    return entry_hash, chain_hash


class LogRequest(BaseModel):
    type: str = Field(min_length=1, max_length=64)
    payload: dict
    timestamp: str = ""

    @field_validator("type")
    @classmethod
    def strip_type(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("empty type")
        return v


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


def rejected(reason: str, status: int = 400) -> JSONResponse:
    print("rejected: %s" % reason, flush=True)
    return JSONResponse({"status": "rejected", "reason": reason}, status_code=status)


@app.exception_handler(RequestValidationError)
async def invalid_body(_request: Request, _exc: RequestValidationError) -> JSONResponse:
    return rejected("invalid request")


@app.exception_handler(StarletteHTTPException)
async def http_error(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
    if exc.status_code == 405:
        return rejected("method not allowed", 405)
    if exc.status_code == 404:
        return rejected("no such endpoint", 404)
    return rejected("rejected", exc.status_code)


def install_live(content: bytes, receipt: dict) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(RUN_PATH)) or ".", exist_ok=True)
    tmp = RUN_PATH + ".tmp"
    with open(tmp, "wb") as f:
        f.write(content)
    os.replace(tmp, RUN_PATH)
    rtmp = RECEIPT_PATH + ".tmp"
    with open(rtmp, "w", encoding="utf-8") as f:
        json.dump(receipt, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(rtmp, RECEIPT_PATH)


@app.post("/log")
async def log_entry(request: Request) -> Any:
    raw = await request.body()
    if not valid_log_sig(raw, request.headers.get("x-witness-signature", "")):
        return rejected("unauthorized", 401)
    try:
        req = LogRequest.model_validate_json(raw)
    except Exception:
        return rejected("invalid request")
    if req.type != "weight_deploy":
        return rejected("unsupported type: protocol updates are not accepted from this API")

    payload = dict(req.payload)
    weights_sha = str(payload.get("sha256", "")).strip().lower()
    presented_pin = str(payload.get("protocol_sha256", "")).strip().lower()
    if not HEX64.match(weights_sha) or not HEX64.match(presented_pin):
        return rejected("payload.sha256 and payload.protocol_sha256 must be 64-char hex")

    try:
        pin = protocol_pin()
    except OSError:
        return rejected("protocol file unreadable", status=500)
    if presented_pin != pin:
        return rejected("protocol pin mismatch")

    content_b64 = payload.pop("content_b64", None)
    content = None
    if content_b64 is not None:
        try:
            content = base64.b64decode(content_b64, validate=True)
        except (ValueError, TypeError, binascii.Error):
            return rejected("invalid content_b64")
        if len(content) > MAX_WEIGHT_BYTES:
            return rejected("weight file too large")
        if sha256_hex(content) != weights_sha:
            return rejected("content hash does not match payload.sha256")

    filename = os.path.basename(str(payload.get("filename") or "weights.bin"))
    blob = {
        "type": "weight_deploy",
        "payload": {
            "sha256": weights_sha,
            "protocol_sha256": pin,
            "filename": filename,
            "bytes": len(content) if content is not None else int(payload.get("bytes") or 0),
        },
        "timestamp": now_utc(),
    }
    try:
        entry_hash, chain_hash = append(blob)
    except sqlite3.IntegrityError:
        return rejected("duplicate entry")
    except Exception:
        return rejected("append failed", status=500)

    body = {
        "status": "received",
        "chain_hash": chain_hash,
        "entry_hash": entry_hash,
        "weights_sha256": weights_sha,
        "protocol_sha256": pin,
        "issued_at": blob["timestamp"],
    }
    body["signature"] = sign_receipt({k: body[k] for k in SIGN_KEYS})
    if content is not None:
        try:
            install_live(content, body)
        except OSError:
            return rejected("failed to install live weights", status=500)
    return body


def verify() -> int:
    rows = _conn.execute(
        "SELECT id, canonical, entry_hash, prev_hash, chain_hash FROM chain ORDER BY id"
    ).fetchall()
    prev = GENESIS
    ok = True
    if not rows:
        print("CHAIN EMPTY  head=%s" % GENESIS)
        return 0
    for row_id, canonical_json, entry_hash, prev_hash, chain_hash in rows:
        recomputed = sha256_hex(canonical_json.encode("utf-8"))
        linked = sha256_hex((prev + entry_hash).encode("ascii"))
        problems = []
        if recomputed != entry_hash:
            problems.append("entry_hash mismatch")
        if prev_hash != prev:
            problems.append("prev_hash does not match previous chain_hash")
        if linked != chain_hash:
            problems.append("chain_hash mismatch")
        if problems:
            ok = False
            print("BREAK id=%s  %s" % (row_id, "; ".join(problems)))
        else:
            blob = json.loads(canonical_json)
            print(
                "OK    id=%s  type=%s  protocol=%s  chain=%s"
                % (row_id, blob.get("type", "?"), blob.get("payload", {}).get("protocol_sha256", "?")[:12], chain_hash)
            )
        prev = chain_hash
    print("CHAIN %s  n=%d  head=%s" % ("INTACT" if ok else "BROKEN", len(rows), prev))
    print("PROTOCOL PIN %s  file=%s" % (protocol_pin(), PROTOCOL_FILE))
    return 0 if ok else 1


def tamper() -> int:
    row = _conn.execute("SELECT id, canonical FROM chain ORDER BY id LIMIT 1").fetchone()
    if not row:
        print("nothing to tamper — chain is empty")
        return 1
    row_id, canonical_json = row
    blob = json.loads(canonical_json)
    blob["payload"] = {"tampered": True}
    _conn.execute("UPDATE chain SET canonical=? WHERE id=?", (canonical(blob).decode("utf-8"), row_id))
    _conn.commit()
    print("corrupted payload on id=%s (hashes left unchanged)" % row_id)
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "serve"
    if cmd == "pin":
        print(protocol_pin())
        sys.exit(0)
    if cmd == "verify":
        sys.exit(verify())
    if cmd == "tamper":
        sys.exit(tamper())
    if cmd != "serve":
        print("usage: witness.py [serve|pin|verify|tamper]", file=sys.stderr)
        sys.exit(2)
    pin = protocol_pin()
    hmac_key()
    log_key()
    print("protocol_file=%s" % PROTOCOL_FILE, flush=True)
    print("protocol_pin=%s" % pin, flush=True)
    print("run_path=%s" % RUN_PATH, flush=True)
    if not os.environ.get("WITNESS_LOG_KEY"):
        print("log_key_file=%s" % LOG_KEY_FILE, flush=True)
    import uvicorn

    uvicorn.run(app, host=HOST, port=PORT, log_level="info")
