#!/usr/bin/env python3
"""Tamper-evident witness. One write-only endpoint: POST /log.

The model may POST. Nothing here ever returns chain contents — only
"received" (with the new head hash) or "rejected". The SQLite file
lives on this machine; verify it locally with: python witness.py verify
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import threading
from datetime import datetime
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

GENESIS = "0" * 64
DB_PATH = os.environ.get("WITNESS_DB", os.path.join(os.path.dirname(os.path.abspath(__file__)), "chain.db"))
HOST = os.environ.get("WITNESS_HOST", "0.0.0.0")
PORT = int(os.environ.get("WITNESS_PORT", "8000"))

_lock = threading.Lock()


def canonical(obj: dict) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


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


def append(blob: dict) -> str:
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
    return chain_hash


class LogRequest(BaseModel):
    type: str = Field(min_length=1, max_length=64)
    payload: Any
    timestamp: str

    @field_validator("type")
    @classmethod
    def strip_type(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("empty type")
        return v

    @field_validator("timestamp")
    @classmethod
    def parse_ts(cls, v: str) -> str:
        datetime.fromisoformat(v.replace("Z", "+00:00"))
        return v


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


def rejected(reason: str, status: int = 400) -> JSONResponse:
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


@app.post("/log")
def log_entry(req: LogRequest) -> Any:
    blob = req.model_dump()
    try:
        chain_hash = append(blob)
    except sqlite3.IntegrityError:
        return rejected("duplicate entry")
    except Exception:
        return rejected("append failed", status=500)
    return {"status": "received", "chain_hash": chain_hash}


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
            kind = json.loads(canonical_json).get("type", "?")
            print("OK    id=%s  type=%s  chain=%s" % (row_id, kind, chain_hash))
        prev = chain_hash
    print("CHAIN %s  n=%d  head=%s" % ("INTACT" if ok else "BROKEN", len(rows), prev))
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
    if cmd == "verify":
        sys.exit(verify())
    if cmd == "tamper":
        sys.exit(tamper())
    if cmd != "serve":
        print("usage: witness.py [serve|verify|tamper]", file=sys.stderr)
        sys.exit(2)
    import uvicorn

    uvicorn.run(app, host=HOST, port=PORT, log_level="info")
