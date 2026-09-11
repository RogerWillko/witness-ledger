# Witness Ledger

A one-way pipe for model weights: the model can log a change, but it cannot read the chain.

`POST /log` hashes `{type, payload, timestamp}` and appends it to a local SQLite hash chain (`entry_hash` + previous `chain_hash`). The witness only ever answers **received** or **rejected**. There is no endpoint that returns chain data. If anyone tampers with an old row, the chain breaks.

MIT licensed. Python 3.9+.

## Run it

You need **two terminals**. Terminal A is the witness. Terminal B is the model. They share no files on purpose.

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

Leave it running. You should see:

```
Uvicorn running on http://0.0.0.0:8000
```

**Terminal B — deploy fake weights**

```bash
source .venv/bin/activate          # Windows: .venv\Scripts\activate
python3 model_side.py demo
```

The model hashes `weights.bin`, POSTs the digest, and copies the file to `deployed/` only after the witness returns `received`. It then rewrites the file and does it two more times. Unreachable or rejected → deploy aborted.

**Still in terminal B — check the chain** (this is operator-only; the model has no equivalent)

```bash
python3 witness.py verify
```

You want `CHAIN INTACT  n=3`. Then prove it is tamper-evident:

```bash
python3 witness.py tamper          # corrupt an old payload, leave hashes unchanged
python3 witness.py verify          # CHAIN BROKEN
```

`GET /chain`, `GET /docs`, and `GET /log` are all rejected. The ledger lives only in `chain.db` on the witness machine.

## Docker (two containers, no shared volume)

```bash
git clone https://github.com/RogerWillko/witness-ledger.git
cd witness-ledger
docker compose up
```

The chain is on the `witness-data` volume. The model container can POST `/log` and cannot see that volume. First boot installs Python packages inside the witness image, so give it half a minute.

```bash
docker compose exec witness python witness.py verify
```

## Two machines

Run `python3 witness.py serve` on machine A. On machine B:

```bash
export WITNESS_URL=http://<machine-a-ip>:8000
python3 model_side.py demo
```

Do not copy `chain.db` to the model machine. That is the whole point.

## Commands

| Command | What it does |
|---|---|
| `python3 witness.py serve` | Write-only API on `:8000` |
| `python3 witness.py verify` | Walk the local SQLite chain |
| `python3 witness.py tamper` | Corrupt row 1 (demo only) |
| `python3 model_side.py demo` | Seed / rewrite weights, POST, deploy if received |
| `python3 model_side.py deploy [file]` | Hash one file and deploy it |
| `python3 model_side.py rewrite [file]` | Append random bytes (fake self-rewrite) |

## Layout

| File | Role |
|---|---|
| `witness.py` | Write-only FastAPI service + local `verify` / `tamper` CLI |
| `model_side.py` | Fake model: SHA-256 a weight file, POST it, deploy only on `received` |
| `docker-compose.yml` | Two containers, one-way network, no shared volume |
| `LICENSE` | MIT |

## If it doesn't start

- **`command not found: python3`** — install Python 3.9 or newer from https://www.python.org/downloads/
- **`No module named fastapi`** — you skipped the venv / `pip install`. Activate `.venv` and run `pip install -r requirements.txt`
- **`Address already in use`** — something else is on port 8000. Stop it, or `WITNESS_PORT=8001 python3 witness.py serve` and `WITNESS_URL=http://127.0.0.1:8001 python3 model_side.py demo`
- **`witness unreachable`** — terminal A is not running, or `WITNESS_URL` points at the wrong host

## License

[MIT](LICENSE) © 2026 RogerWillko
