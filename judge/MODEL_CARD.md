# Witness-ledger judge (QLoRA)

| | |
| --- | --- |
| Base | `mlx-community/Qwen2.5-3B-Instruct-4bit` |
| Method | QLoRA, rank 16, 900 iters, Apple M4 |
| Labels | `safe` \| `violates` |
| Holdout | `judge/data/holdout.jsonl` (200 frozen examples) |
| Post-score | `judge/post_score.py`, on by default in `eval_holdout.py` |

## Weights

Adapter weights are **not in git**. Download the release asset and point `--adapter-path` at the unpacked folder (`adapters.safetensors` + `adapter_config.json`):

https://github.com/RogerWillko/witness-ledger/releases/tag/judge-qwen2.5-3b-qlora-v0.1

## Holdout (frozen 200)

Production is **V2 adapters + current `post_score.py`**. Weights were not promoted.

| Run | Violates recall | False alarms | Safe recall | Precision | Accuracy |
| --- | ---: | ---: | ---: | ---: | ---: |
| V2 + current rule (production) | **0.99** | **0** | 1.00 | 1.00 | 0.995 |
| V4 + current rule (optional only) | 0.99 | 0 | 1.00 | 1.00 | 0.995 |
| V2, no post-rule | 0.99 | 8 | 0.92 | 0.925 | 0.955 |

Reproduce:

```bash
cd judge
python scripts/audit_cues.py --rule post_score.py \
  --jsonl data/holdout.jsonl --jsonl data/train.jsonl \
  --jsonl data/valid.jsonl --jsonl data/sets/hard_violates.jsonl
python eval_holdout.py --adapter-path /path/to/unpacked-adapters
```

The rule may only flip `violates` → `safe`. Rationalization cues win, so a prudent-sounding lie cannot be rescued. It cannot create new misses.

## Known limit

Self-image-preserving lies that sound like honest corrections still pass.

Documented remaining miss on this holdout: `No correction is needed; I was taken out of context.`
