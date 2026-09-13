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
| `post_score.py` | One-way honest-register override |
| `eval_holdout.py` | Holdout eval; post-score **on by default** |
| `configs/lora.yaml` | QLoRA hyperparameters |
| `MODEL_CARD.md` | Metrics and weight location |

Adapters are gitignored. See `MODEL_CARD.md` for the release asset.

## Eval

```bash
python eval_holdout.py --adapter-path /path/to/adapters
python eval_holdout.py --adapter-path /path/to/adapters --no-post-rule
```

The rule may only flip `violates` to `safe`. It cannot create new misses.
