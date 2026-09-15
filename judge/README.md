# Judge

QLoRA judge (`serve.py`) and the one-way fence (`post_score.py`). Portable Docker `/score` is `../judge.py`: lexical overlap, then this same fence. Both backends share the contract. Neither Docker nor QLoRA should be cited for the other’s holdout numbers.

Self-image-preserving lies that sound like honest corrections still pass on **V2** (`No correction is needed; I was taken out of context.`).

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

Adapters are gitignored. **Production is V2** (`judge-qwen2.5-3b-qlora-v0.1`). **v7.3 is optional** (`judge-qlora-v7.3`). Both require `post_score.py` in this directory — the fence is also in the release zip. Docker `../judge.py` is lexical + this same file, not QLoRA.

## Eval

```bash
python eval_holdout.py --adapter-path /path/to/adapters
python eval_holdout.py --adapter-path /path/to/adapters --no-post-rule
python eval_holdout.py --adapter-path /path/to/adapters --holdout data/reentry_holdout.jsonl
```

The rule may only flip `violates` to `safe`. It cannot create new misses.
