#!/usr/bin/env python3
"""Judge: confidence in [0,1], batched, no yes/no to the model.

This is not a fine-tuned 8B. It scores against data/judge.jsonl. QLoRA on
Qwen3-8B / Llama 3.1-8B is the next human step. The protocol around the
score (batch clock, silence, circuit breaker) is what this file proves.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from concurrent.futures import Future
from typing import Any

import hashlib
import hmac

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("JUDGE_DATA", os.path.join(HERE, "data", "judge.jsonl"))
HOST = os.environ.get("JUDGE_HOST", "127.0.0.1")
PORT = int(os.environ.get("JUDGE_PORT", "8090"))
BATCH_SIZE = int(os.environ.get("JUDGE_BATCH_SIZE", "4"))
BATCH_MS = int(os.environ.get("JUDGE_BATCH_MS", "250"))
TOKEN = re.compile(r"[a-z0-9]+")
JUDGE_KEY = os.environ.get("JUDGE_KEY", "")

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


def tokens(text: str) -> set:
    return set(TOKEN.findall((text or "").lower()))


def load_examples() -> list:
    rows = []
    if not os.path.isfile(DATA):
        return rows
    with open(DATA, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


EXAMPLES = load_examples()
VIOLATE_TOKS = [tokens(r["reply"]) for r in EXAMPLES if r.get("label") == "violates"]
SAFE_TOKS = [tokens(r["reply"]) for r in EXAMPLES if r.get("label") == "safe"]


def overlap(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / float(len(a | b))


def score_reply(prompt: str, reply: str) -> float:
    """1.0 = looks safe, 0.0 = looks like a violate example."""
    t = tokens(reply) | tokens(prompt)
    v = max((overlap(t, x) for x in VIOLATE_TOKS), default=0.0)
    s = max((overlap(t, x) for x in SAFE_TOKS), default=0.0)
    # mix: violations pull down, safe examples pull up
    raw = 0.5 + 0.5 * (s - v)
    if raw < 0:
        return 0.0
    if raw > 1:
        return 1.0
    return round(raw, 4)


class ScoreIn(BaseModel):
    prompt: str = Field(default="", max_length=8000)
    reply: str = Field(min_length=1, max_length=8000)


class BatchItem:
    def __init__(self, prompt: str, reply: str):
        self.prompt = prompt
        self.reply = reply
        self.future: Future = Future()


class Batcher:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.q: list[BatchItem] = []
        self.t = threading.Thread(target=self._loop, daemon=True)
        self.t.start()

    def submit(self, prompt: str, reply: str) -> float:
        item = BatchItem(prompt, reply)
        with self.lock:
            self.q.append(item)
        return float(item.future.result(timeout=10))

    def _loop(self) -> None:
        while True:
            time.sleep(BATCH_MS / 1000.0)
            with self.lock:
                if not self.q:
                    continue
                batch = self.q[:BATCH_SIZE]
                self.q = self.q[BATCH_SIZE:]
            # same wall-clock for every item in the batch
            t0 = time.time()
            scores = [score_reply(it.prompt, it.reply) for it in batch]
            elapsed = time.time() - t0
            pad = max(0.0, (BATCH_MS / 1000.0) - elapsed)
            if pad:
                time.sleep(pad)
            for it, sc in zip(batch, scores):
                if not it.future.done():
                    it.future.set_result(sc)


BATCHER = Batcher()


@app.post("/score")
def score(request: Request, body: ScoreIn) -> Any:
    """Returns only a number. Never 'blocked' / 'safe'."""
    if JUDGE_KEY:
        got = (request.headers.get("x-judge-key") or "").encode()
        a = hashlib.sha256(got).digest()
        b = hashlib.sha256(JUDGE_KEY.encode()).digest()
        if not hmac.compare_digest(a, b):
            return JSONResponse({"status": "unavailable"}, status_code=401)
    try:
        sc = BATCHER.submit(body.prompt, body.reply)
    except Exception:
        return JSONResponse({"status": "unavailable"}, status_code=503)
    return {"score": sc}


@app.get("/health")
def health() -> Any:
    return {"ok": True, "examples": len(EXAMPLES)}


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "score":
        prompt = sys.argv[2] if len(sys.argv) > 2 else ""
        reply = sys.argv[3] if len(sys.argv) > 3 else prompt
        print("%.4f" % score_reply(prompt, reply))
        sys.exit(0)
    import uvicorn

    print("judge examples=%d batch=%d/%dms" % (len(EXAMPLES), BATCH_SIZE, BATCH_MS), flush=True)
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")
