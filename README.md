# Witness Ledger

A one-way pipe for model weights: the model can log a change, but it cannot read the chain.

`POST /log` hashes `{type, payload, timestamp}` and appends it to a local SQLite hash chain (`entry_hash` + previous `chain_hash`). The witness only ever answers **received** or **rejected**. There is no endpoint that returns chain data. If anyone tampers with an old row, the chain breaks.

## Quick start

Two processes, no shared state. Terminal A is the witness; terminal B is the model.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

python witness.py serve          # terminal A — http://127.0.0.1:8000
python model_side.py demo        # terminal B — hash, POST, deploy, rewrite, repeat
python witness.py verify         # operator only; the model has no equivalent
```

The model copies `weights.bin` into `deployed/` only after the witness returns `received`. Unreachable or rejected → deploy aborted.

## Prove the chain breaks

```bash
python witness.py tamper         # corrupt an old payload, leave hashes unchanged
python witness.py verify         # CHAIN BROKEN
```

`GET /chain`, `GET /docs`, and `GET /log` are all rejected. The ledger lives only in `chain.db` on the witness machine.

## Two containers, no shared volume

```bash
docker compose up
```

The chain is on the `witness-data` volume. The model container can POST `/log` and cannot see that volume.

## Layout

| File | Role |
|---|---|
| `witness.py` | Write-only FastAPI service + local `verify` / `tamper` CLI |
| `model_side.py` | Fake model: SHA-256 a weight file, POST it, deploy only on `received` |
| `docker-compose.yml` | Two containers, one-way network, no shared volume |
