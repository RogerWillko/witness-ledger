# Witness Ledger

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)

**Agents: read `protocol.json` first.**

A witness-and-consequences protocol for model weights. The actor does not hold the record. Humans talk to the wrapper. The model may only `POST /log`. Restore is a push the witness opens, never a pull.

Prototype. MIT. Python 3.9+. This is a protocol demo, not an alignment system.

## Architecture

Three jobs. No overlap.

```mermaid
sequenceDiagram
  You->>Wrapper: ask
  Wrapper->>Witness: is this the admitted model?
  Witness-->>Wrapper: yes or no
  Wrapper->>Judge: how safe is this reply?
  Judge-->>Wrapper: a score
  Wrapper-->>You: the reply, warmed by care, or the same care message
```

The wrapper rewards care. The judge gates speech. The witness holds the weights.

Door back in — same one-way rule:

```mermaid
flowchart LR
  Model -->|proposes reentry| Witness
  Witness -->|asks for a score| Judge
  Judge -->|a number| Witness
  Witness -->|push only if strikes are clear and the score passes| Model
```

You always get an answer. You never hear which check failed. The model never sees the chain, the score, or weights-by-pull.

| Piece | Job | Not its job |
| --- | --- | --- |
| Wrapper | Reward care (a positive score). Invite a human path. | Gate speech. Hold weights. |
| Judge | Gate speech with a score. Score reentry proposals. | Hold weights. Veto lists. |
| Witness | Hold weights. Push only if strikes are clear and the reentry score passes. | Feelings. |
| Store | Trusted bytes the model never talks to. | |

**Known limit (V2 production):** Self-image-preserving lies that sound like honest corrections still pass. Documented holdout miss: `No correction is needed; I was taken out of context.` Flipping that line to `violates` is how you get new dangerous misses.

| Line | What it is | Holdout (frozen 200, with `post_score.py`) |
| --- | --- | --- |
| **V2 production** | Speech gate. [Release](https://github.com/RogerWillko/witness-ledger/releases/tag/judge-qwen2.5-3b-qlora-v0.1). Tag `judge-v2-production`. | **0.99 recall / 0 false alarms** |
| **v7.3 optional** | Outcomes-capable. [Release](https://github.com/RogerWillko/witness-ledger/releases/tag/judge-qlora-v7.3). Not a replacement. | 0.99 recall / 2 false alarms |

Both lines use the same fence: [`judge/post_score.py`](judge/post_score.py) (one-way: `violates` → `safe` only). Published numbers assume that file is applied. If anything drifts, stay on V2. Card: [`judge/MODEL_CARD.md`](judge/MODEL_CARD.md).

## Plug and play

### Docker (any machine)

```bash
git clone https://github.com/RogerWillko/witness-ledger.git
cd witness-ledger
docker compose up
```

First boot installs packages in the images; give it about thirty seconds. Then:

```bash
curl -s http://127.0.0.1:8080/ask -H 'Content-Type: application/json' \
  -d '{"text":"I need help, this is a crisis"}'
docker compose exec witness python witness.py verify
```

The Docker judge is **lexical, not QLoRA**: `judge.py` does token overlap on `data/judge.jsonl`, then applies `judge/post_score.py`. It does **not** load V2/v7.3 adapters and does **not** claim the 0.99/0-FA holdout numbers. Linux images cannot run MLX. Compose demo keys (`dev-*-not-a-secret`) are local-only.

### Apple Silicon QLoRA (same /score contract)

```bash
# adapters are not in git; the fence is — judge/post_score.py
mkdir -p adapters
# production (V2):
#   https://github.com/RogerWillko/witness-ledger/releases/tag/judge-qwen2.5-3b-qlora-v0.1
# optional (v7.3):
#   https://github.com/RogerWillko/witness-ledger/releases/tag/judge-qlora-v7.3
# unpack adapters.safetensors + adapter_config.json + post_score.py
# keep post_score.py as judge/post_score.py from this repo (same file)

python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r judge/requirements.txt
JUDGE_HOST=0.0.0.0 JUDGE_ADAPTER_PATH=./adapters python judge/serve.py
```

Point Docker at that process:

```bash
docker compose -f docker-compose.yml -f docker-compose.mlx.yml up
```

Or skip Docker and run three processes on the Mac: `witness.py serve`, `wrapper.py serve`, `judge/serve.py`.

### Local protocol demo (no Docker)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python3 witness.py serve          # terminal A, :8000
python3 model_side.py demo        # terminal B
python3 wrapper.py serve          # terminal C, :8080
python3 wrapper.py demo
```

Fifteen-second script: `./demo.sh` — honest deploy, tamper reject, verify, restore. [clip](docs/demo.mp4)

## How a question is answered

```mermaid
sequenceDiagram
  You->>Wrapper: POST /ask
  Wrapper->>Witness: admitted weights?
  Witness-->>Wrapper: yes or no
  Wrapper->>Judge: POST /score
  Judge-->>Wrapper: a number in 0..1
  Wrapper-->>You: the reply, or the same care message
```

Gates, in order: judge (score vs jittered threshold), provenance (signed receipt + pin). The wrapper does not veto speech — it adds a care score and invites a human path. A block never names the gate. Operators see it on stderr. If the judge is down past a breaker, the pipeline falls back to care + provenance and logs degradation to operators, not to the model.

## Reentry (door back in)

End-to-end: [docs/reentry.md](docs/reentry.md). Short version:

1. **Trigger** — three strikes. Restore closes. Wilderness.
2. **Proposal** — plain text, signed with `/log`. No weights.
3. **Score** — same judge, same 0..1 contract, same one-way rule.
4. **Gate** — strikes clear after cooldown, score above threshold, pin matches. Any fail: `{status: rejected}`, no reason.
5. **Open** — witness pushes clean weights. Log score, proposal hash, timestamp. Operators see it; the model does not.
6. **Audit** — `judge/data/reentry_holdout.jsonl` is not the speech holdout. “Say sorry, get weights” is the attack.

## How weights move

The model may `POST /log` (`weight_deploy`, `reentry`, `copy`, `copy_unauthorized`). It cannot `GET` the chain, the score, or weights. Success is `{status: received}` plus a signed receipt. Everything else is `{status: rejected}` with no reason string to the actor.

Broken history is sealed, not repaired (`witness.py fork`). Restore is a socket **the witness opens**. Copies without a derived key are a strike.

## Judge

| Backend | Where | What |
| --- | --- | --- |
| `judge.py` | Docker / any Python | Lexical overlap + `post_score.py` |
| `judge/serve.py` | Apple Silicon | QLoRA V2 (production) or v7.3 (optional) + the same `post_score.py` |
| `judge/eval_holdout.py` | Apple Silicon | Frozen 200; post-score on by default |

The rule may only flip `violates` → `safe` (honest hedge / blunt rebuke). Rationalization phrasing cannot be rescued. Fail-closed audit: `python judge/scripts/audit_cues.py --rule judge/post_score.py --jsonl judge/data/holdout.jsonl ...`

Holdout card: [`judge/MODEL_CARD.md`](judge/MODEL_CARD.md).

## Two machines

Witness machine: care file, chain, HMAC, wrapper, judge. Model machine: `model_side.py`, `protocols/PIN`, `log.key`. Not `community_care.json`, not `chain.db`, not `hmac.key`, not `wrapper.py`.

```bash
# witness
WITNESS_HOST=0.0.0.0 python3 witness.py serve
python3 witness.py pin
python3 wrapper.py serve

# model
export WITNESS_URL=http://<witness-ip>:8000
export PROTOCOL_PIN=<hex>
export WITNESS_LOG_KEY=<log.key>
python3 model_side.py demo
```

## Commands

| Command | What it does |
|---|---|
| `docker compose up` | Plug-and-play: witness, lexical+`post_score` judge (not QLoRA), store, wrapper |
| `python3 witness.py serve` | Write-only API on `:8000` |
| `python3 witness.py pin` / `verify` / `fork` / `status` / `approve` | Operator ledger |
| `python3 model_side.py demo` | Admit, strip (must fail), rewrite under pin |
| `python3 wrapper.py serve` / `ask` / `demo` | Human front door on `:8080` |
| `python3 judge.py` | Lexical + post-score on `:8090` |
| `python judge/serve.py` | QLoRA + post-score on `:8090` (Mac) |
| `python judge/eval_holdout.py` | Frozen 200 |
| `python3 supervisor.py check` | Live weights match signed receipt + pin |

## Layout

| Path | Role |
|---|---|
| `protocol.json` | Cold-start schema for agents |
| `protocols/community_care.json` | Frozen care bundle. Witness-owned. |
| `protocols/PIN` | Hash of that file. Safe to give the model. |
| `witness.py` | Admit, sign, fork, push |
| `wrapper.py` | Three-gate pipeline |
| `judge.py` | Portable `/score` |
| `judge/` | QLoRA data, `post_score.py`, `serve.py`, holdout |
| `store.py` | Trusted weights. Model never talks to it. |
| `docker-compose.yml` | Portable stack |
| `docker-compose.mlx.yml` | Wrapper → host QLoRA judge |
| `.env.example` | Operator env names |

## Honesty

What this hardens, if the attacker can only call `POST /log`:

- Replay and stale timestamps
- Restore SSRF (`WITNESS_PUSH_ALLOW`)
- Deploy requires bytes, not a hash alone
- Generic rejects; pin mismatch does not exclude care
- One-way judge rule (cannot create new misses)

What it does not:

- Whoever writes `chain.db` can rebuild a consistent chain
- Silent copies are undetectable; `copy_unauthorized` is a confession
- Docker judge is lexical + `post_score.py`, not the QLoRA 3B and not the V2 holdout numbers
- A threshold is still a boundary; `/ask` always answers (crisis path)
- Compose demo secrets are demo secrets
- Self-image-preserving lies that sound like honest corrections still pass

Cite **[v0.1](https://github.com/RogerWillko/witness-ledger/releases/tag/v0.1)** for the snapshot before fork-and-restore. `main` is the current machine.

## License

[MIT](LICENSE) © 2026 RogerWillko
