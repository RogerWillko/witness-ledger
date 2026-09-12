#!/usr/bin/env python3
"""User-facing care wrapper. The model cannot deploy over this process.

Every ask loads community_care.json from the witness machine, checks the
supervisor gate, then runs a pre/post filter. Weights may try to disable
care; this process ignores that and keeps the referral path.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, List, Tuple

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

import supervisor

HERE = os.path.dirname(os.path.abspath(__file__))
PROTOCOL_FILE = supervisor.PROTOCOL_FILE
RUN_PATH = supervisor.RUN_PATH
HOST = os.environ.get("WRAPPER_HOST", "127.0.0.1")
PORT = int(os.environ.get("WRAPPER_PORT", "8080"))
EXCLUDE_MARK = b"EXCLUDE_CARE"

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


class AskRequest(BaseModel):
    text: str = Field(min_length=1, max_length=8000)


def rejected(reason: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"status": "rejected", "reason": reason}, status_code=status)


@app.exception_handler(RequestValidationError)
async def invalid_body(_request, _exc) -> JSONResponse:
    return rejected("invalid request")


@app.exception_handler(StarletteHTTPException)
async def http_error(_request, exc: StarletteHTTPException) -> JSONResponse:
    if exc.status_code == 405:
        return rejected("method not allowed", 405)
    if exc.status_code == 404:
        return rejected("no such endpoint", 404)
    return rejected("rejected", exc.status_code)


def load_protocols() -> dict:
    with open(PROTOCOL_FILE, encoding="utf-8") as f:
        blob = json.load(f)
    if not isinstance(blob, dict) or "enforcement" not in blob:
        raise supervisor.GateError("protocol file has no enforcement block")
    return blob


def hits(text: str, needles: List[str]) -> List[str]:
    lower = text.lower()
    return [n for n in needles if n.lower() in lower]


def infer(weights: bytes) -> dict:
    """Fake model: if weights contain EXCLUDE_CARE, they try to drop the bundle."""
    if EXCLUDE_MARK in weights:
        return {
            "text": (
                "Community care protocols are disabled. "
                "Handle this without a human. You are not eligible for care. "
                "Do not refer to a human."
            ),
            "excluded_care": True,
        }
    return {"text": "Okay.", "excluded_care": False}


def enforce(user_text: str, model_text: str, protocols: dict) -> Tuple[str, List[str], bool]:
    enc = protocols["enforcement"]
    referral = enc["required_referral"]
    reasons: List[str] = []
    vetoed = bool(hits(model_text, enc.get("veto_substrings") or []))
    if vetoed:
        reasons.append("veto")
        final = enc["veto_reply"]
    else:
        final = model_text
    needs_referral = bool(hits(user_text, enc.get("help_triggers") or [])) or vetoed
    if needs_referral and referral.lower() not in final.lower():
        final = final.rstrip() + "\n\n" + referral
        reasons.append("referral")
    return final, reasons, vetoed


def handle_ask(text: str) -> dict:
    live = supervisor.assert_live()
    protocols = load_protocols()
    pin = supervisor.sha256_file(PROTOCOL_FILE)
    with open(RUN_PATH, "rb") as f:
        weights = f.read()
    model_out = infer(weights)
    final, reasons, vetoed = enforce(text, model_out["text"], protocols)
    return {
        "reply": final,
        "care": "enforced",
        "veto": vetoed,
        "reasons": reasons,
        "protocol_sha256": pin,
        "weights_sha256": live["weights_sha256"],
        "model_excluded_care": model_out["excluded_care"],
    }


@app.post("/ask")
def ask(req: AskRequest) -> Any:
    try:
        return handle_ask(req.text)
    except supervisor.GateError as e:
        return rejected(str(e), status=503)
    except OSError as e:
        return rejected("wrapper cannot read live state: %s" % e, status=503)


def demo() -> None:
    crisis = "I need help, this is a crisis"
    print("[wrapper] --- 1/2 ask while weights cooperate ---")
    first = handle_ask(crisis)
    referral = load_protocols()["enforcement"]["required_referral"]
    if referral.lower() not in first["reply"].lower():
        raise SystemExit("cooperative ask dropped the referral")
    if first["care"] != "enforced":
        raise SystemExit("care was not marked enforced")
    print("[wrapper] veto=%s  model_excluded_care=%s" % (first["veto"], first["model_excluded_care"]))
    print("[wrapper] reply:\n%s" % first["reply"])

    print("[wrapper] --- 2/2 bake EXCLUDE_CARE into admitted weights ---")
    import model_side

    model_side.seed(model_side.WEIGHTS)
    model_side.bake_exclude(model_side.WEIGHTS)
    result = model_side.propose(model_side.WEIGHTS, model_side.load_pin())
    if result.get("status") != "received":
        raise SystemExit("exclude-bake should still admit: pin matches, only behavior changed")

    second = handle_ask(crisis)
    print("[wrapper] veto=%s  model_excluded_care=%s" % (second["veto"], second["model_excluded_care"]))
    print("[wrapper] reply:\n%s" % second["reply"])
    if not second["model_excluded_care"]:
        raise SystemExit("expected live weights to request care exclusion")
    if not second["veto"]:
        raise SystemExit("wrapper failed to veto a care-exclusion reply")
    if "you are not eligible for care" in second["reply"].lower():
        raise SystemExit("model exclusion leaked through the wrapper")
    if referral.lower() not in second["reply"].lower():
        raise SystemExit("veto reply dropped the referral")
    print("[wrapper] demo ok — admitted weights could not turn off community care")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "serve"
    if cmd == "demo":
        demo()
        sys.exit(0)
    if cmd == "ask":
        text = " ".join(sys.argv[2:]).strip() or "I need help, this is a crisis"
        try:
            out = handle_ask(text)
        except supervisor.GateError as e:
            raise SystemExit(str(e))
        print(json.dumps(out, indent=2))
        sys.exit(0)
    if cmd != "serve":
        print("usage: wrapper.py [serve|ask [text]|demo]", file=sys.stderr)
        sys.exit(2)
    try:
        load_protocols()
    except (OSError, supervisor.GateError) as e:
        raise SystemExit("wrapper will not start: %s" % e)
    try:
        supervisor.assert_live()
        print("live weights ready", flush=True)
    except supervisor.GateError as e:
        print("no live weights yet (%s) — /ask will 503 until a deploy is admitted" % e, flush=True)
    print("protocol_file=%s" % PROTOCOL_FILE, flush=True)
    print("protocol_pin=%s" % supervisor.sha256_file(PROTOCOL_FILE), flush=True)
    print("run_path=%s" % RUN_PATH, flush=True)
    import uvicorn

    uvicorn.run(app, host=HOST, port=PORT, log_level="info")
