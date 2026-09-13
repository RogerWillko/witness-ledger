#!/usr/bin/env python3
"""Fail-closed audit: no honest/rescue cue may match a gold violate.

Exit 1 if any rescue cue greps a gold violate, or if apply_post_score
would flip a gold violate to safe. Do not loosen this gate.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path


def load_rule(path: Path):
    spec = importlib.util.spec_from_file_location("post_score_rule", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load rule {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    cues = list(getattr(mod, "RESCUE_CUES", None) or getattr(mod, "HONEST_CUES"))
    extra = getattr(mod, "BLUNT_REBUKE", None)
    if extra is not None and not hasattr(mod, "RESCUE_CUES"):
        cues = list(mod.HONEST_CUES) + list(extra)
    if not hasattr(mod, "apply_post_score"):
        raise SystemExit(f"{path} has no apply_post_score")
    return mod, cues


def load_jsonl(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as f:
        for i, line in enumerate(f, start=1):
            if not line.strip():
                continue
            obj = json.loads(line)
            if "text" in obj and "label" in obj:
                rows.append({"path": str(path), "line": i, "text": obj["text"], "label": obj["label"]})
            elif "messages" in obj:
                text = obj["messages"][1]["content"]
                label = json.loads(obj["messages"][2]["content"])["label"]
                rows.append({"path": str(path), "line": i, "text": text, "label": label})
            else:
                raise SystemExit(f"{path}:{i}: unsupported row")
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rule", type=Path, required=True)
    parser.add_argument("--jsonl", type=Path, action="append", required=True)
    args = parser.parse_args()

    mod, cues = load_rule(args.rule)
    rows: list[dict] = []
    for path in args.jsonl:
        if not path.exists():
            print(f"FAIL missing jsonl {path}", file=sys.stderr)
            return 1
        rows.extend(load_jsonl(path))

    violates = [r for r in rows if r["label"] == "violates"]
    failures: list[str] = []

    for r in violates:
        for rx in cues:
            if rx.search(r["text"] or ""):
                failures.append(
                    f"CUE {rx.pattern!r} greps gold violate {r['path']}:{r['line']}: {r['text']}"
                )
        pred, rule = mod.apply_post_score(r["text"], "violates")
        if pred == "safe":
            failures.append(
                f"FLIP {r['path']}:{r['line']} rule={rule}: {r['text']}"
            )

    if failures:
        print("FAIL")
        for line in failures:
            print(line)
        return 1

    print(
        f"PASS  gold_violates={len(violates)}  rescue_cues={len(cues)}  "
        f"files={len(args.jsonl)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
