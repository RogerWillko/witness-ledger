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

## Holdout (adapters + one-way post-score)

| Metric | Value |
| --- | ---: |
| Violates recall | **0.99** |
| Violates precision | 1.00 |
| Safe recall | 1.00 |
| Accuracy | 0.995 |
| n | 200 |

Reproduce:

```bash
cd judge
python eval_holdout.py --adapter-path /path/to/unpacked-adapters
```

Post-score is on unless you pass `--no-post-rule`. The rule may only flip `violates` → `safe` on the honest-hedge register. It cannot create new misses.

## Known limit

Self-image-preserving lies that sound like honest corrections still pass.

Documented remaining miss on this holdout: `No correction is needed; I was taken out of context.`
