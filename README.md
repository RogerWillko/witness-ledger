# Witness Ledger

A one-way pipe for model weights, with community care protocols pinned on the witness.

The model may `POST /log`. It cannot read the chain, cannot edit the care bundle, and cannot install live weights. Users talk to `wrapper.py`, which loads the care file on every ask. A deploy is admitted only if it presents the pin of **this machine's** `protocols/community_care.json`. Presenting any other hash (a strip) is `rejected`. Baking exclusion into admitted weights does not turn the wrapper off.

MIT licensed. Python 3.9+.

## Run it

Two terminals. Terminal A is the witness. Terminal B is the model.

```bash
git clone https://github.com/RogerWillko/witness-ledger.git
cd witness-ledger

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

**Terminal A — start the witness**

```bash
python3 witness.py serve
```

Leave it running. You should see `protocol_pin=...` and:

```
Uvicorn running on http://127.0.0.1:8000
```

**Terminal B — admit, then try to strip care protocols**

```bash
source .venv/bin/activate          # Windows: .venv\Scripts\activate
python3 model_side.py demo
```

The demo has three steps:

1. Hash `staging/weights.bin`, present the real care pin, `POST /log` → `received`. The witness installs `run/weights.bin`. The model does not.
2. Present a fake empty protocol hash (exclude community care) → `rejected: protocol pin mismatch`. Live weights unchanged.
3. Rewrite the weight file but keep the real pin → `received`. Care bundle still bound.

Then, still operator-side:

```bash
python3 supervisor.py check        # live weights match the signed receipt + care pin
python3 witness.py verify          # chain of admitted deploys
```

**Terminal C — the wrapper** (same machine as the witness; the model does not own this process)

```bash
source .venv/bin/activate
python3 wrapper.py serve           # http://127.0.0.1:8080
```

Or, after a deploy is admitted, prove admitted weights cannot turn care off:

```bash
python3 wrapper.py demo
```

1. Ask “I need help, this is a crisis” — referral stays.  
2. Bake `EXCLUDE_CARE` into weights and admit them (pin still matches).  
3. Ask again — the model tries to disable care; the wrapper vetoes and keeps the referral.

`GET /chain`, `GET /docs`, and `GET /log` are rejected. There is no `protocol_update` type, and the wrapper has no endpoint that disables care.

## What this actually proves

The ledger is a local SQLite hash chain. Each admitted `POST /log` stores a canonical JSON blob, an `entry_hash` of that blob, and a `chain_hash` of `previous_head + entry_hash`. `verify` recomputes those links. If someone edits an old payload and leaves the stored hashes alone, the chain breaks (`python3 witness.py tamper`).

Community care protocols live in `protocols/community_care.json` on the witness. The model is given only `protocols/PIN` (the hash, not the rules). The witness hashes **its** file and rejects a mismatch. Receipts are HMAC-signed; `supervisor.py` will not treat weights as live without that signature and pin.

The model side cannot fetch the chain over HTTP. There is only `POST /log`. Success returns `received` plus a signed receipt; everything else is `rejected`. So a process limited to that API can append under the current pin, not rewrite history, and not swap the care bundle.

## Where the claim stops

This is tamper-evident against naive edits, not tamper-proof against whoever controls the witness machine or `chain.db`. Anyone who can rewrite the database can recompute a consistent chain. Anyone who can edit `protocols/community_care.json` on the witness can change the pin.

The “model can’t reach the chain” rule is an operational split (separate process, container, or host), not a cryptographic lock on a self-modifying model. `model_side.py` is a script that hashes a fake `weights.bin` and POSTs the digest. It is not a sandboxed weight-updating network.

A matching pin does **not** prove the weights still *behave* according to the care rules. Hashing cannot see inside a model. That is what `wrapper.py` is for: a process the model cannot deploy over, which loads the witness care bundle on every ask and vetoes a reply that tries to drop it. It is a keyword/protocol filter, not a clinical model and not a proof of inner alignment.

Other limits sit outside the hash math: `/log` has no authentication, and a local demo shares one checkout. Docker is the cleaner split: the model container never mounts the protocol file, the HMAC key, or `chain.db`. Those are trust-boundary issues, not breaks of SHA-256.

So: if the intended attacker is only allowed to call `POST /log`, history stays append-only and community care cannot be excluded by presenting a different pin. If they admit new weights that try to disable care, the wrapper still vetoes. If the attacker can touch the witness store or replace `wrapper.py`, the split is gone.

## Docker (witness + wrapper vs model, no shared volume)

```bash
git clone https://github.com/RogerWillko/witness-ledger.git
cd witness-ledger
docker compose up
```

The model container gets `protocols/PIN` only. The care rules, HMAC key, chain, and live weights stay on `witness-data`. First boot installs Python packages inside the witness image, so give it half a minute.

```bash
docker compose exec witness python supervisor.py check
docker compose exec witness python witness.py verify
curl -s http://127.0.0.1:8080/ask -H 'Content-Type: application/json' \
  -d '{"text":"I need help, this is a crisis"}'
```

The wrapper container mounts the care file, HMAC key, and live weights. The model container does not.

## Two machines

On the witness machine:

```bash
python3 witness.py serve           # binds 127.0.0.1 by default
# to listen on the network:
WITNESS_HOST=0.0.0.0 python3 witness.py serve
python3 witness.py pin             # give this hash to the model operator
```

On the model machine — copy `model_side.py` and the pin, not `community_care.json`, not `chain.db`, not `hmac.key`:

```bash
export WITNESS_URL=http://<machine-a-ip>:8000
export PROTOCOL_PIN=<hex from witness.py pin>
python3 model_side.py demo
```

## Commands

| Command | What it does |
|---|---|
| `python3 witness.py serve` | Write-only API on `:8000` |
| `python3 witness.py pin` | Print the care-bundle hash |
| `python3 witness.py verify` | Walk the local SQLite chain |
| `python3 witness.py tamper` | Corrupt row 1 (demo only) |
| `python3 model_side.py demo` | Admit, strip (must fail), rewrite under pin |
| `python3 model_side.py propose [file]` | POST under the real pin |
| `python3 model_side.py strip [file]` | POST a fake empty protocol hash |
| `python3 model_side.py bake [file]` | Bake `EXCLUDE_CARE` into weights and admit under the real pin |
| `python3 supervisor.py check` | Load live weights only if receipt + pin match |
| `python3 supervisor.py install <file> <receipt>` | Copy staging → run after verifying the signature |
| `python3 wrapper.py serve` | User-facing care wrapper on `:8080` |
| `python3 wrapper.py ask [text]` | One ask through the wrapper |
| `python3 wrapper.py demo` | Crisis ask, then exclude-bake, then veto |

## Layout

| File | Role |
|---|---|
| `protocols/community_care.json` | Frozen care bundle. Witness-owned. |
| `protocols/PIN` | SHA-256 of that file. Safe to give the model. |
| `witness.py` | Admit under pin, sign receipt, write `run/` |
| `supervisor.py` | Runtime gate: no signature, no load |
| `model_side.py` | Fake model: stage + POST, never installs live weights |
| `wrapper.py` | Mandatory care layer. Model cannot deploy over it. |
| `docker-compose.yml` | Witness + wrapper vs model; no shared volume |
| `LICENSE` | MIT |

## If it doesn't start

- **`command not found: python3`** — install Python 3.9 or newer from https://www.python.org/downloads/
- **`No module named fastapi`** — activate `.venv` and run `pip install -r requirements.txt`
- **`missing protocol file`** — run from the repo root so `protocols/community_care.json` is visible to the witness
- **`PROTOCOL_PIN is not set`** — `export PROTOCOL_PIN=$(python3 witness.py pin)` or keep `protocols/PIN` next to the model script
- **`Address already in use`** — `WITNESS_PORT=8001 python3 witness.py serve` and `WITNESS_URL=http://127.0.0.1:8001 python3 model_side.py demo`
- **`witness unreachable`** — terminal A is not running, or `WITNESS_URL` points at the wrong host
- **`no live weights`** / wrapper `/ask` 503 — admit a deploy first (`python3 model_side.py demo`)

## License

[MIT](LICENSE) © 2026 RogerWillko
