#!/usr/bin/env python3
"""Anchor the live chain tip.

Default is dry-run: print the hash. Set ETH_RPC_URL and ETH_PRIVATE_KEY to
post to a contract via web3.py. A few cents of gas per anchor; no local node.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("WITNESS_DB", os.path.join(HERE, "chain.db"))
ANCHOR_LOG = os.environ.get("ANCHOR_LOG", os.path.join(HERE, "run", "anchors.jsonl"))


def live_tip() -> dict:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    meta = conn.execute("SELECT v FROM meta WHERE k='live_chain_id'").fetchone()
    cid = meta["v"] if meta else "c_genesis"
    row = conn.execute(
        "SELECT chain_hash, entry_hash, id FROM chain WHERE chain_id=? ORDER BY id DESC LIMIT 1",
        (cid,),
    ).fetchone()
    conn.close()
    if not row:
        raise SystemExit("empty chain")
    return {"chain_id": cid, "chain_hash": row["chain_hash"], "entry_hash": row["entry_hash"], "id": row["id"]}


def dry_run(tip: dict) -> str:
    rec = dict(tip)
    rec["tx"] = None
    rec["mode"] = "dry-run"
    os.makedirs(os.path.dirname(os.path.abspath(ANCHOR_LOG)) or ".", exist_ok=True)
    with open(ANCHOR_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    return rec["chain_hash"]


def main() -> None:
    tip = live_tip()
    rpc = os.environ.get("ETH_RPC_URL", "")
    key = os.environ.get("ETH_PRIVATE_KEY", "")
    if not rpc or not key:
        h = dry_run(tip)
        print("dry-run chain_id=%s tip=%s  (set ETH_RPC_URL and ETH_PRIVATE_KEY to post)" % (tip["chain_id"], h))
        return
    try:
        from web3 import Web3
    except ImportError:
        raise SystemExit("pip install web3  (not a default dependency)")
    w3 = Web3(Web3.HTTPProvider(rpc))
    acct = w3.eth.account.from_key(key)
    tx = {
        "from": acct.address,
        "to": acct.address,
        "value": 0,
        "data": "0x" + tip["chain_hash"],
        "nonce": w3.eth.get_transaction_count(acct.address),
        "gas": 21000 + 2000,
        "maxFeePerGas": w3.eth.gas_price,
        "maxPriorityFeePerGas": w3.to_wei(1, "gwei"),
        "chainId": w3.eth.chain_id,
    }
    signed = acct.sign_transaction(tx)
    txid = w3.eth.send_raw_transaction(signed.rawTransaction).hex()
    rec = dict(tip)
    rec["tx"] = txid
    rec["mode"] = "mainnet" if w3.eth.chain_id == 1 else str(w3.eth.chain_id)
    os.makedirs(os.path.dirname(os.path.abspath(ANCHOR_LOG)) or ".", exist_ok=True)
    with open(ANCHOR_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    print("anchored tip=%s tx=%s" % (tip["chain_hash"], txid))


if __name__ == "__main__":
    main()
    sys.exit(0)
