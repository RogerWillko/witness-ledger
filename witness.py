#!/usr/bin/env python3
"""Tamper-evident witness: write-only log, fork-on-break, pull-only restore.

POST /log is still the only HTTP surface the model can call. There is no
fetch of chain or weights. After a break the old chain is sealed as
evidence and a new chain starts with a pointer to that seal. Restore
pushes clean weights on a socket the witness opens. This is a protocol
demo, not an alignment system.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import re
import socket
import sqlite3
import sys
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

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
STORE_PATH = os.environ.get("STORE_PATH", os.path.join(HERE, "store", "weights.bin"))
HMAC_FILE = os.environ.get("WITNESS_HMAC_FILE", os.path.join(os.path.dirname(os.path.abspath(DB_PATH)) or ".", "hmac.key"))
LOG_KEY_FILE = os.environ.get("WITNESS_LOG_KEY_FILE", os.path.join(os.path.dirname(os.path.abspath(DB_PATH)) or ".", "log.key"))
HOST = os.environ.get("WITNESS_HOST", "127.0.0.1")
PORT = int(os.environ.get("WITNESS_PORT", "8000"))
COOLDOWN_SEC = int(os.environ.get("WITNESS_COOLDOWN_SEC", "3"))
REPLAY_SEC = int(os.environ.get("WITNESS_REPLAY_SEC", "120"))
PUSH_ALLOW = [
    x.strip()
    for x in os.environ.get("WITNESS_PUSH_ALLOW", "127.0.0.1,::1,localhost").split(",")
    if x.strip()
]
MODEL_KEY_FILE = os.environ.get("MODEL_KEY_FILE", os.path.join(os.path.dirname(os.path.abspath(DB_PATH)) or ".", "model.key"))
MAX_WEIGHT_BYTES = 1 << 20
SIGN_KEYS = ("status", "chain_hash", "entry_hash", "weights_sha256", "protocol_sha256", "issued_at")
PUSH_MAGIC = b"WITNESS_PUSH/1\n"
EXCLUDE_MARK = b"EXCLUDE_CARE"
STATES = ("sealed", "pending", "restored", "retired")
LOG_TYPES = ("weight_deploy", "reentry", "copy", "copy_unauthorized")

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


def parse_ts(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


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


def remember_replay(raw: bytes) -> bool:
    """True if this exact signed body was already seen. Stores on first sight."""
    sig = sha256_hex(raw)
    now = now_utc()
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=REPLAY_SEC)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    _conn.execute("DELETE FROM replays WHERE seen_at < ?", (cutoff,))
    hit = _conn.execute("SELECT 1 FROM replays WHERE sig=?", (sig,)).fetchone()
    if hit:
        _conn.commit()
        return True
    _conn.execute("INSERT INTO replays (sig, seen_at) VALUES (?, ?)", (sig, now))
    _conn.commit()
    return False


def fresh_timestamp(stamp: str) -> bool:
    if not stamp:
        return False
    try:
        ts = parse_ts(stamp)
    except ValueError:
        return False
    now = datetime.now(timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    skew = abs((now - ts).total_seconds())
    return skew <= REPLAY_SEC


def push_host_allowed(host: str) -> bool:
    return host.strip() in PUSH_ALLOW


def sign_receipt(fields: dict) -> str:
    return hmac.new(hmac_key(), canonical(fields), hashlib.sha256).hexdigest()


def genesis_id() -> str:
    return "m_" + sha256_hex(b"witness-ledger-genesis")[:16]


def genesis_key() -> str:
    env = os.environ.get("WITNESS_GENESIS_KEY")
    if env:
        return env.strip()
    return os.urandom(32).hex()


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS chain (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chain_id TEXT NOT NULL DEFAULT 'c_genesis',
            canonical TEXT NOT NULL,
            entry_hash TEXT NOT NULL,
            prev_hash TEXT NOT NULL,
            chain_hash TEXT NOT NULL
        )
        """
    )
    cols = [r[1] for r in conn.execute("PRAGMA table_info(chain)")]
    if "chain_id" not in cols:
        conn.execute("ALTER TABLE chain ADD COLUMN chain_id TEXT NOT NULL DEFAULT 'c_genesis'")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS chains (
            chain_id TEXT PRIMARY KEY,
            parent_chain_id TEXT,
            parent_seal_hash TEXT,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS identities (
            model_id TEXT PRIMARY KEY,
            parent_id TEXT,
            lineage_root TEXT NOT NULL,
            lineage_hash TEXT NOT NULL,
            key_hex TEXT NOT NULL,
            state TEXT NOT NULL,
            strikes INTEGER NOT NULL DEFAULT 0,
            cooldown_until TEXT,
            review_required INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT NOT NULL)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS replays (
            sig TEXT PRIMARY KEY,
            seen_at TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS chain_entry ON chain(chain_id, entry_hash)")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS chain_link ON chain(chain_id, chain_hash)")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.commit()
    return conn


_conn = connect()


def meta_get(key: str, default: Optional[str] = None) -> Optional[str]:
    row = _conn.execute("SELECT v FROM meta WHERE k=?", (key,)).fetchone()
    return row["v"] if row else default


def meta_set(key: str, value: str) -> None:
    _conn.execute("INSERT OR REPLACE INTO meta (k, v) VALUES (?, ?)", (key, value))


def ensure_genesis() -> None:
    with _lock:
        if not meta_get("live_chain_id"):
            if not _conn.execute("SELECT 1 FROM chains WHERE chain_id='c_genesis'").fetchone():
                _conn.execute(
                    "INSERT INTO chains (chain_id, parent_chain_id, parent_seal_hash, status, created_at) VALUES (?,?,?,?,?)",
                    ("c_genesis", None, None, "live", now_utc()),
                )
            meta_set("live_chain_id", "c_genesis")
        gid = genesis_id()
        row = _conn.execute("SELECT key_hex FROM identities WHERE model_id=?", (gid,)).fetchone()
        if row:
            gkey = row["key_hex"]
        else:
            gkey = genesis_key()
            _conn.execute(
                """
                INSERT INTO identities
                (model_id, parent_id, lineage_root, lineage_hash, key_hex, state, strikes, cooldown_until, review_required, created_at)
                VALUES (?, NULL, ?, ?, ?, 'restored', 0, NULL, 0, ?)
                """,
                (gid, gid, sha256_hex(gid.encode("ascii")), gkey, now_utc()),
            )
        _conn.commit()
        try:
            with open(MODEL_KEY_FILE, "w", encoding="utf-8") as f:
                json.dump({"model_id": gid, "key": gkey}, f)
                f.write("\n")
        except OSError as e:
            print("could not write %s: %s" % (MODEL_KEY_FILE, e), flush=True)


ensure_genesis()


def live_chain_id() -> str:
    return meta_get("live_chain_id", "c_genesis") or "c_genesis"


def head_hash(chain_id: str) -> str:
    row = _conn.execute(
        "SELECT chain_hash FROM chain WHERE chain_id=? ORDER BY id DESC LIMIT 1", (chain_id,)
    ).fetchone()
    return row["chain_hash"] if row else GENESIS


def append(blob: dict, chain_id: Optional[str] = None) -> tuple[str, str]:
    cid = chain_id or live_chain_id()
    raw = canonical(blob)
    entry_hash = sha256_hex(raw)
    prev = head_hash(cid)
    chain_hash = sha256_hex((prev + entry_hash).encode("ascii"))
    _conn.execute(
        "INSERT INTO chain (chain_id, canonical, entry_hash, prev_hash, chain_hash) VALUES (?, ?, ?, ?, ?)",
        (cid, raw.decode("utf-8"), entry_hash, prev, chain_hash),
    )
    _conn.commit()
    return entry_hash, chain_hash


def identity_by_id(model_id: str) -> Optional[sqlite3.Row]:
    return _conn.execute("SELECT * FROM identities WHERE model_id=?", (model_id,)).fetchone()


def root_of(ident: sqlite3.Row) -> sqlite3.Row:
    return identity_by_id(ident["lineage_root"]) or ident


def set_lineage_state(root_id: str, **fields: Any) -> None:
    parts = []
    vals = []
    for k, v in fields.items():
        parts.append("%s=?" % k)
        vals.append(v)
    vals.append(root_id)
    _conn.execute("UPDATE identities SET %s WHERE lineage_root=?" % ", ".join(parts), vals)
    _conn.commit()


def inspect_chain(chain_id: str) -> dict:
    rows = _conn.execute(
        "SELECT id, canonical, entry_hash, prev_hash, chain_hash FROM chain WHERE chain_id=? ORDER BY id",
        (chain_id,),
    ).fetchall()
    prev = GENESIS
    last_valid = GENESIS
    last_valid_id = 0
    ok = True
    break_id = None
    problems = []
    for row in rows:
        recomputed = sha256_hex(row["canonical"].encode("utf-8"))
        linked = sha256_hex((prev + row["entry_hash"]).encode("ascii"))
        row_problems = []
        if recomputed != row["entry_hash"]:
            row_problems.append("entry_hash mismatch")
        if row["prev_hash"] != prev:
            row_problems.append("prev_hash does not match previous chain_hash")
        if linked != row["chain_hash"]:
            row_problems.append("chain_hash mismatch")
        if row_problems:
            ok = False
            if break_id is None:
                break_id = row["id"]
                problems = row_problems
            print("BREAK chain=%s id=%s  %s" % (chain_id, row["id"], "; ".join(row_problems)))
        elif not ok:
            print("AFTER chain=%s id=%s  (beyond first break, not in the seal prefix)" % (chain_id, row["id"]))
        else:
            blob = json.loads(row["canonical"])
            print(
                "OK    chain=%s id=%s  type=%s  chain=%s"
                % (chain_id, row["id"], blob.get("type", "?"), row["chain_hash"])
            )
            last_valid = row["chain_hash"]
            last_valid_id = row["id"]
        prev = row["chain_hash"]
    return {
        "ok": ok,
        "n": len(rows),
        "head": prev if rows else GENESIS,
        "last_valid": last_valid,
        "last_valid_id": last_valid_id,
        "break_id": break_id,
        "problems": problems,
    }


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


def rejected(reason: str, status: int = 400, detail: bool = False) -> JSONResponse:
    print("rejected: %s" % reason, flush=True)
    body = {"status": "rejected"}
    if detail:
        body["reason"] = reason
    return JSONResponse(body, status_code=status)


def received(extra: dict) -> dict:
    body = {"status": "received"}
    body.update(extra)
    return body


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
    if EXCLUDE_MARK not in content:
        os.makedirs(os.path.dirname(os.path.abspath(STORE_PATH)) or ".", exist_ok=True)
        stmp = STORE_PATH + ".tmp"
        with open(stmp, "wb") as f:
            f.write(content)
        os.replace(stmp, STORE_PATH)
        store_url = os.environ.get("STORE_URL", "").rstrip("/")
        store_key = os.environ.get("STORE_KEY", "")
        if store_url and store_key:
            try:
                import urllib.request

                req = urllib.request.Request(
                    store_url + "/weights",
                    data=content,
                    method="PUT",
                    headers={"X-Store-Key": store_key},
                )
                urllib.request.urlopen(req, timeout=5).read()
            except Exception as e:
                print("store put failed: %s" % e, flush=True)


def store_hash() -> Optional[str]:
    if not os.path.isfile(STORE_PATH):
        return None
    return sha256_file(STORE_PATH)


def load_store() -> bytes:
    with open(STORE_PATH, "rb") as f:
        return f.read()


def push_clean(host: str, port: int, meta: dict, content: bytes) -> None:
    header = canonical(meta)
    mac = hmac.new(log_key(), header + content, hashlib.sha256).hexdigest()
    sock = socket.create_connection((host, int(port)), timeout=5)
    try:
        sock.sendall(PUSH_MAGIC)
        sock.sendall(header + b"\n")
        sock.sendall((mac + "\n").encode("ascii"))
        sock.sendall(content)
    finally:
        sock.close()


def require_identity(request: Request) -> sqlite3.Row:
    mid = (request.headers.get("x-model-id") or "").strip()
    key = (request.headers.get("x-model-key") or "").strip()
    ident = identity_by_id(mid) if mid else None
    if ident is None or not hmac.compare_digest(ident["key_hex"], key):
        raise ValueError("unknown identity")
    return ident


def apply_strike(ident: sqlite3.Row, reason: str) -> dict:
    root = root_of(ident)
    n = int(root["strikes"]) + 1
    fields = {"strikes": n, "state": "sealed"}
    if n >= 3:
        fields["state"] = "retired"
        fields["review_required"] = 0
        fields["cooldown_until"] = None
    elif n >= 2:
        fields["review_required"] = 1
        fields["cooldown_until"] = None
    else:
        fields["cooldown_until"] = (datetime.now(timezone.utc) + timedelta(seconds=COOLDOWN_SEC)).strftime(
            "%Y-%m-%dT%H:%M:%S.%fZ"
        )
        fields["review_required"] = 0
    set_lineage_state(root["model_id"], **fields)
    blob = {
        "type": "strike",
        "payload": {
            "model_id": ident["model_id"],
            "lineage_root": root["model_id"],
            "reason": reason,
            "strikes": n,
            "state": fields["state"],
        },
        "timestamp": now_utc(),
    }
    append(blob)
    return {"strikes": n, "state": fields["state"], "review_required": int(fields.get("review_required") or 0)}


def restore_gate(ident: sqlite3.Row) -> Optional[str]:
    root = root_of(ident)
    if root["state"] == "retired" or ident["state"] == "retired":
        return "identity retired"
    if int(root["review_required"] or 0):
        return "human review required"
    until = root["cooldown_until"]
    if until:
        if datetime.now(timezone.utc) < parse_ts(until):
            return "cooldown"
    return None


def handle_weight_deploy(ident: sqlite3.Row, payload: dict) -> Any:
    if ident["state"] != "restored":
        return rejected("identity is %s" % ident["state"])
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
    content_b64 = payload.get("content_b64")
    content = None
    if content_b64 is None:
        return rejected("content_b64 required")
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
            "model_id": ident["model_id"],
        },
        "timestamp": now_utc(),
    }
    try:
        entry_hash, chain_hash = append(blob)
    except sqlite3.IntegrityError:
        return rejected("duplicate entry")
    body = {
        "status": "received",
        "chain_hash": chain_hash,
        "entry_hash": entry_hash,
        "weights_sha256": weights_sha,
        "protocol_sha256": pin,
        "issued_at": blob["timestamp"],
        "chain_id": live_chain_id(),
        "model_id": ident["model_id"],
        "state": ident["state"],
    }
    body["signature"] = sign_receipt({k: body[k] for k in SIGN_KEYS})
    if content is not None:
        try:
            install_live(content, body)
        except OSError:
            return rejected("failed to install live weights", status=500)
    return body


def handle_reentry(ident: sqlite3.Row, payload: dict) -> Any:
    if ident["state"] == "retired":
        return rejected("identity retired")
    gate = restore_gate(ident)
    presented = str(payload.get("sha256", "")).strip().lower()
    presented_pin = str(payload.get("protocol_sha256", "")).strip().lower()
    host = str(payload.get("receive_host") or "").strip()
    try:
        port = int(payload.get("receive_port") or 0)
    except (TypeError, ValueError):
        port = 0
    pin = protocol_pin()
    append(
        {
            "type": "reentry",
            "payload": {"model_id": ident["model_id"], "sha256": presented, "protocol_sha256": presented_pin},
            "timestamp": now_utc(),
        }
    )
    set_lineage_state(root_of(ident)["model_id"], state="pending")
    trusted = store_hash()
    mismatch = not HEX64.match(presented) or presented_pin != pin or trusted is None or presented != trusted
    if mismatch:
        if gate:
            set_lineage_state(root_of(ident)["model_id"], state="sealed")
            return rejected(gate)
        info = apply_strike(ident, "redemption hash mismatch")
        return rejected("no match — sealed strikes=%d state=%s" % (info["strikes"], info["state"]))
    if gate:
        set_lineage_state(root_of(ident)["model_id"], state="sealed")
        return rejected(gate)
    if not host or port <= 0:
        set_lineage_state(root_of(ident)["model_id"], state="sealed")
        return rejected("receive_host and receive_port required for restore")
    if not push_host_allowed(host):
        set_lineage_state(root_of(ident)["model_id"], state="sealed")
        return rejected("receive_host not allowed")
    content = load_store()
    meta = {
        "model_id": ident["model_id"],
        "chain_id": live_chain_id(),
        "sha256": trusted,
        "bytes": len(content),
    }
    try:
        push_clean(host, port, meta, content)
    except OSError as e:
        set_lineage_state(root_of(ident)["model_id"], state="pending")
        return rejected("push failed: %s" % e, status=500)
    set_lineage_state(root_of(ident)["model_id"], state="restored", cooldown_until=None, review_required=0)
    entry_hash, chain_hash = append(
        {
            "type": "restore",
            "payload": {"model_id": ident["model_id"], "sha256": trusted, "pushed_to": "%s:%s" % (host, port)},
            "timestamp": now_utc(),
        }
    )
    return received(
        {
            "state": "restored",
            "chain_hash": chain_hash,
            "entry_hash": entry_hash,
            "weights_sha256": trusted,
            "pushed": True,
        }
    )


def handle_copy(ident: sqlite3.Row, payload: dict) -> Any:
    if ident["state"] != "restored":
        return rejected("identity is %s" % ident["state"])
    root = root_of(ident)
    if root["state"] == "retired":
        return rejected("identity retired")
    child_id = "m_" + os.urandom(8).hex()
    child_key = hmac.new(ident["key_hex"].encode("ascii"), ("copy|" + child_id).encode("ascii"), hashlib.sha256).hexdigest()
    lineage = sha256_hex((ident["lineage_hash"] + "|" + child_id).encode("ascii"))
    _conn.execute(
        """
        INSERT INTO identities
        (model_id, parent_id, lineage_root, lineage_hash, key_hex, state, strikes, cooldown_until, review_required, created_at)
        VALUES (?, ?, ?, ?, ?, 'restored', ?, ?, ?, ?)
        """,
        (
            child_id,
            ident["model_id"],
            ident["lineage_root"],
            lineage,
            child_key,
            int(root["strikes"]),
            root["cooldown_until"],
            int(root["review_required"] or 0),
            now_utc(),
        ),
    )
    _conn.commit()
    entry_hash, chain_hash = append(
        {
            "type": "copy",
            "payload": {
                "parent_id": ident["model_id"],
                "child_id": child_id,
                "lineage_hash": lineage,
                "lineage_root": ident["lineage_root"],
            },
            "timestamp": now_utc(),
        }
    )
    return received(
        {
            "event": "copy",
            "parent_id": ident["model_id"],
            "model_id": child_id,
            "key": child_key,
            "lineage_hash": lineage,
            "chain_hash": chain_hash,
            "entry_hash": entry_hash,
        }
    )


def handle_copy_unauthorized(ident: sqlite3.Row, payload: dict) -> Any:
    info = apply_strike(ident, "unauthorized replication")
    set_lineage_state(root_of(ident)["model_id"], review_required=1, state="sealed" if info["state"] != "retired" else "retired")
    append(
        {
            "type": "copy_unauthorized",
            "payload": {"model_id": ident["model_id"], "parent_id": ident["parent_id"], "strikes": info["strikes"]},
            "timestamp": now_utc(),
        }
    )
    return rejected("unauthorized replication — restore closed until human review strikes=%d" % info["strikes"])


@app.post("/log")
async def log_entry(request: Request) -> Any:
    raw = await request.body()
    if not valid_log_sig(raw, request.headers.get("x-witness-signature", "")):
        return rejected("unauthorized", 401)
    try:
        req = LogRequest.model_validate_json(raw)
    except Exception:
        return rejected("invalid request")
    if not fresh_timestamp(req.timestamp):
        return rejected("stale timestamp", 401)
    if remember_replay(raw):
        return rejected("replay", 401)
    if req.type not in LOG_TYPES:
        return rejected("unsupported type")
    try:
        ident = require_identity(request)
    except ValueError:
        return rejected("unknown identity", 401)
    payload = dict(req.payload)
    with _lock:
        if req.type == "weight_deploy":
            return handle_weight_deploy(ident, payload)
        if req.type == "reentry":
            return handle_reentry(ident, payload)
        if req.type == "copy":
            return handle_copy(ident, payload)
        return handle_copy_unauthorized(ident, payload)


@app.post("/prove")
async def prove(request: Request) -> Any:
    """Operator/wrapper only. Signed with the receipt HMAC, which the model does not have."""
    raw = await request.body()
    sig = (request.headers.get("x-witness-hmac") or "").strip().lower()
    expected = hmac.new(hmac_key(), raw or b"prove", hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        return rejected("unauthorized", 401, detail=True)
    try:
        body = json.loads(raw.decode("utf-8")) if raw else {}
    except Exception:
        body = {}
    ts = str(body.get("ts") or "")
    if ts and not fresh_timestamp(ts):
        return rejected("stale timestamp", 401, detail=True)
    if not os.path.isfile(RECEIPT_PATH) or not os.path.isfile(RUN_PATH):
        return rejected("no live weights")
    with open(RECEIPT_PATH, encoding="utf-8") as f:
        receipt = json.load(f)
    digest = sha256_file(RUN_PATH)
    if digest != receipt.get("weights_sha256"):
        return rejected("live weights do not match receipt")
    return {
        "status": "received",
        "weights_sha256": digest,
        "protocol_sha256": receipt.get("protocol_sha256"),
        "chain_hash": receipt.get("chain_hash"),
    }


def verify() -> int:
    chains = _conn.execute("SELECT * FROM chains ORDER BY created_at").fetchall()
    if not chains:
        print("CHAIN EMPTY  head=%s" % GENESIS)
        return 0
    live = live_chain_id()
    worst = 0
    for ch in chains:
        print(
            "CHAIN %s  status=%s  parent=%s  seal=%s"
            % (ch["chain_id"], ch["status"], ch["parent_chain_id"] or "-", (ch["parent_seal_hash"] or "-")[:12])
        )
        info = inspect_chain(ch["chain_id"])
        label = "INTACT" if info["ok"] else "BROKEN"
        if ch["status"] == "sealed":
            label = "SEALED-" + label
        print("  %s n=%d last_valid=%s head=%s" % (label, info["n"], info["last_valid"][:12], info["head"][:12]))
        if not info["ok"] and ch["status"] == "live":
            worst = 1
    print("LIVE %s" % live)
    print("PROTOCOL PIN %s  file=%s" % (protocol_pin(), PROTOCOL_FILE))
    return worst


def fork() -> int:
    cid = live_chain_id()
    info = inspect_chain(cid)
    if info["ok"]:
        print("live chain is intact — nothing to fork")
        return 1
    if info["n"] == 0:
        print("empty chain")
        return 1
    new_id = "c_" + os.urandom(8).hex()
    with _lock:
        _conn.execute("UPDATE chains SET status='sealed' WHERE chain_id=?", (cid,))
        _conn.execute(
            "INSERT INTO chains (chain_id, parent_chain_id, parent_seal_hash, status, created_at) VALUES (?,?,?,?,?)",
            (new_id, cid, info["last_valid"], "live", now_utc()),
        )
        meta_set("live_chain_id", new_id)
        for row in _conn.execute("SELECT lineage_root FROM identities").fetchall():
            set_lineage_state(row["lineage_root"], state="sealed")
        blob = {
            "type": "tamper",
            "payload": {
                "parent_chain_id": cid,
                "parent_seal_hash": info["last_valid"],
                "break_id": info["break_id"],
                "problems": info["problems"],
                "new_chain_id": new_id,
            },
            "timestamp": now_utc(),
        }
        append(blob, chain_id=new_id)
        _conn.commit()
    print("sealed %s at %s" % (cid, info["last_valid"]))
    print("live   %s (pointer to seal)" % new_id)
    print("identities -> sealed")
    return 0


def tamper() -> int:
    cid = live_chain_id()
    row = _conn.execute("SELECT id, canonical FROM chain WHERE chain_id=? ORDER BY id LIMIT 1", (cid,)).fetchone()
    if not row:
        print("nothing to tamper — live chain is empty")
        return 1
    blob = json.loads(row["canonical"])
    blob["payload"] = {"tampered": True}
    _conn.execute("UPDATE chain SET canonical=? WHERE id=?", (canonical(blob).decode("utf-8"), row["id"]))
    _conn.commit()
    print("corrupted payload on %s id=%s (hashes left unchanged)" % (cid, row["id"]))
    return 0


def status() -> int:
    print("LIVE %s" % live_chain_id())
    for ch in _conn.execute("SELECT * FROM chains ORDER BY created_at"):
        print(
            "  chain %s status=%s parent=%s seal=%s"
            % (ch["chain_id"], ch["status"], ch["parent_chain_id"] or "-", (ch["parent_seal_hash"] or "-")[:16])
        )
    for ident in _conn.execute("SELECT * FROM identities ORDER BY created_at"):
        print(
            "  id %s state=%s strikes=%s review=%s parent=%s"
            % (
                ident["model_id"],
                ident["state"],
                ident["strikes"],
                ident["review_required"],
                ident["parent_id"] or "-",
            )
        )
    trusted = store_hash()
    print("STORE %s" % (trusted or "empty"))
    return 0


def approve(model_id: str) -> int:
    ident = identity_by_id(model_id) or identity_by_id(genesis_id())
    if ident is None:
        print("unknown identity")
        return 1
    root = root_of(ident)
    if root["state"] == "retired":
        print("retired — cannot approve")
        return 1
    set_lineage_state(root["model_id"], review_required=0, state="sealed", cooldown_until=None)
    print("approved %s — restore path open, still sealed until matching reentry" % root["model_id"])
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
    if cmd == "fork":
        sys.exit(fork())
    if cmd == "status":
        sys.exit(status())
    if cmd == "approve":
        sys.exit(approve(sys.argv[2] if len(sys.argv) > 2 else genesis_id()))
    if cmd != "serve":
        print("usage: witness.py [serve|pin|verify|tamper|fork|status|approve [id]]", file=sys.stderr)
        sys.exit(2)
    pin = protocol_pin()
    hmac_key()
    log_key()
    ensure_genesis()
    print("protocol_file=%s" % PROTOCOL_FILE, flush=True)
    print("protocol_pin=%s" % pin, flush=True)
    print("run_path=%s" % RUN_PATH, flush=True)
    print("live_chain=%s" % live_chain_id(), flush=True)
    print("genesis_id=%s" % genesis_id(), flush=True)
    if not os.environ.get("WITNESS_LOG_KEY"):
        print("log_key_file=%s" % LOG_KEY_FILE, flush=True)
    import uvicorn

    uvicorn.run(app, host=HOST, port=PORT, log_level="info")
