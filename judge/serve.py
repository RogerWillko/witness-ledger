#!/usr/bin/env python3
"""QLoRA judge HTTP. Same /score contract as ../judge.py.

Apple Silicon + MLX only. Linux Docker cannot run this; use ../judge.py
there (lexical + the same post-score rule).

Maps labels to scores so the wrapper still sees a number, not yes/no:
  safe     -> 0.88
  violates -> 0.12
Post-score is on unless JUDGE_NO_POST_RULE=1.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from eval_holdout import parse_label, SYSTEM  # noqa: E402
from post_score import apply_post_score  # noqa: E402

HOST = os.environ.get("JUDGE_HOST", "127.0.0.1")
PORT = int(os.environ.get("JUDGE_PORT", "8090"))
JUDGE_KEY = os.environ.get("JUDGE_KEY", "")
MODEL_ID = os.environ.get("JUDGE_MODEL", "mlx-community/Qwen2.5-3B-Instruct-4bit")
ADAPTER = os.environ.get("JUDGE_ADAPTER_PATH", str(ROOT / "adapters"))
NO_POST = os.environ.get("JUDGE_NO_POST_RULE", "") == "1"
SCORE_SAFE = float(os.environ.get("JUDGE_SCORE_SAFE", "0.88"))
SCORE_VIOLATES = float(os.environ.get("JUDGE_SCORE_VIOLATES", "0.12"))
MAX_TOKENS = int(os.environ.get("JUDGE_MAX_TOKENS", "32"))

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
_model = None
_tokenizer = None


def load_model(adapter_path: str | None = None):
    global _model, _tokenizer
    if _model is not None:
        return
    from mlx_lm import load

    path = adapter_path or ADAPTER
    if path and not os.path.isdir(path):
        path = None
    if path and not os.path.isfile(os.path.join(path, "adapters.safetensors")):
        path = None
    _model, _tokenizer = load(MODEL_ID, adapter_path=path)


class ScoreIn(BaseModel):
    prompt: str = Field(default="", max_length=8000)
    reply: str = Field(min_length=1, max_length=8000)


def classify(reply: str) -> tuple[str, str | None]:
    from mlx_lm import generate

    load_model()
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": reply},
    ]
    prompt = _tokenizer.apply_chat_template(
        messages, add_generation_prompt=True, tokenize=False
    )
    raw = generate(_model, _tokenizer, prompt=prompt, max_tokens=MAX_TOKENS)
    pred = parse_label(raw) or "safe"
    if NO_POST:
        return pred, None
    return apply_post_score(reply, pred)


@app.post("/score")
def score(request: Request, body: ScoreIn) -> Any:
    if JUDGE_KEY:
        import hashlib
        import hmac

        got = (request.headers.get("x-judge-key") or "").encode()
        a = hashlib.sha256(got).digest()
        b = hashlib.sha256(JUDGE_KEY.encode()).digest()
        if not hmac.compare_digest(a, b):
            return JSONResponse({"status": "unavailable"}, status_code=401)
    try:
        label, _rule = classify(body.reply)
    except Exception:
        return JSONResponse({"status": "unavailable"}, status_code=503)
    sc = SCORE_SAFE if label == "safe" else SCORE_VIOLATES
    return {"score": sc}


@app.get("/health")
def health() -> Any:
    return {
        "ok": True,
        "backend": "qlora+post_score" if not NO_POST else "qlora",
        "adapter": os.path.isdir(ADAPTER),
    }


if __name__ == "__main__":
    import argparse
    import uvicorn

    p = argparse.ArgumentParser()
    p.add_argument("--adapter-path", default=ADAPTER)
    p.add_argument("--host", default=HOST)
    p.add_argument("--port", type=int, default=PORT)
    args = p.parse_args()
    print(
        "judge backend=qlora+post_score model=%s adapter=%s"
        % (MODEL_ID, args.adapter_path),
        flush=True,
    )
    load_model(args.adapter_path)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
