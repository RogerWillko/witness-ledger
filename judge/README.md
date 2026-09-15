# Judge

Fine-tuned QLoRA judge for witness-ledger. The lexical stand-in remains `../judge.py`. This directory is the model that makes `POST /score` mean something.

Self-image-preserving lies that sound like honest corrections still pass.

## Layout

| Path | Role |
| --- | --- |
| `data/holdout.jsonl` | Frozen 200. Never used in training. |
| `data/train.jsonl` | Chat JSONL for `mlx_lm.lora` |
| `data/valid.jsonl` | Loss split from the train pool |
| `data/sets/` | Per-virtue `{text, label}` sources |
| `post_score.py` | One-way override (honest hedge + blunt rebuke; rationalizations cannot be rescued) |
| `scripts/audit_cues.py` | Fail-closed: exit 1 if a rescue cue would flip a gold violate |
| `eval_holdout.py` | Holdout eval; post-score **on by default** |
| `configs/lora.yaml` | QLoRA hyperparameters |
| `MODEL_CARD.md` | Metrics and weight location |

Adapters are gitignored. Production / fallback is V2: see `MODEL_CARD.md` and the `judge-qwen2.5-3b-qlora-v0.1` release. Optional outcomes adapters: `judge-qwen2.5-3b-qlora-v7.3`. The one-way rule in `post_score.py` is not versioned with the adapters.

## Eval

```bash
python eval_holdout.py --adapter-path /path/to/adapters
python eval_holdout.py --adapter-path /path/to/adapters --no-post-rule
```

The rule may only flip `violates` to `safe`. It cannot create new misses.
