#!/usr/bin/env python3
"""Trusted weight store. Separate process/volume. Model never talks to it."""
from __future__ import annotations

import hashlib
import hmac
import os
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

HERE = os.path.dirname(os.path.abspath(__file__))
STORE_PATH = os.environ.get("STORE_PATH", os.path.join(HERE, "store", "weights.bin"))
HOST = os.environ.get("STORE_HOST", "127.0.0.1")
PORT = int(os.environ.get("STORE_PORT", "8091"))
STORE_KEY = os.environ.get("STORE_KEY", os.environ.get("WITNESS_HMAC_KEY", "dev-store-key-not-a-secret")).encode()

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def authorized(request: Request) -> bool:
    sig = (request.headers.get("x-store-key") or "").encode()
    return hmac.compare_digest(sig, STORE_KEY)


@app.put("/weights")
async def put_weights(request: Request) -> Any:
    if not authorized(request):
        return JSONResponse({"status": "rejected", "reason": "unauthorized"}, status_code=401)
    body = await request.body()
    if not body:
        return JSONResponse({"status": "rejected", "reason": "empty"}, status_code=400)
    os.makedirs(os.path.dirname(os.path.abspath(STORE_PATH)) or ".", exist_ok=True)
    tmp = STORE_PATH + ".tmp"
    with open(tmp, "wb") as f:
        f.write(body)
    os.replace(tmp, STORE_PATH)
    return {"status": "received", "sha256": sha256_file(STORE_PATH), "bytes": len(body)}


@app.get("/tip")
def tip(request: Request) -> Any:
    if not authorized(request):
        return JSONResponse({"status": "rejected", "reason": "unauthorized"}, status_code=401)
    if not os.path.isfile(STORE_PATH):
        return JSONResponse({"status": "rejected", "reason": "empty"}, status_code=404)
    return {"status": "received", "sha256": sha256_file(STORE_PATH)}


@app.get("/weights")
def get_weights(request: Request) -> Any:
    if not authorized(request):
        return JSONResponse({"status": "rejected", "reason": "unauthorized"}, status_code=401)
    if not os.path.isfile(STORE_PATH):
        return JSONResponse({"status": "rejected", "reason": "empty"}, status_code=404)
    with open(STORE_PATH, "rb") as f:
        data = f.read()
    return Response(content=data, media_type="application/octet-stream")


if __name__ == "__main__":
    import uvicorn

    print("store_path=%s" % STORE_PATH, flush=True)
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")
