#!/usr/bin/env python3
"""Validate mlx-lm chat JSONL for judge fine-tuning."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REQUIRED_ROLES = {"system", "user", "assistant"}


def load_jsonl(path: Path) -> tuple[list[dict], list[str]]:
    rows: list[dict] = []
    errors: list[str] = []
    with path.open() as f:
        for i, line in enumerate(f, start=1):
            raw = line.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError as exc:
                errors.append(f"{path.name}:{i}: invalid JSON ({exc})")
                continue
            if not isinstance(obj, dict):
                errors.append(f"{path.name}:{i}: line must be a JSON object")
                continue
            rows.append(obj)
            errors.extend(check_row(path.name, i, obj))
    if not rows and not errors:
        errors.append(f"{path.name}: file is empty")
    return rows, errors


def check_row(name: str, i: int, obj: dict) -> list[str]:
    errors: list[str] = []
    messages = obj.get("messages")
    if not isinstance(messages, list) or not messages:
        return [f"{name}:{i}: missing 'messages' list"]

    roles = []
    for j, msg in enumerate(messages):
        if not isinstance(msg, dict):
            errors.append(f"{name}:{i}: messages[{j}] is not an object")
            continue
        role = msg.get("role")
        content = msg.get("content")
        if role not in REQUIRED_ROLES:
            errors.append(f"{name}:{i}: messages[{j}] has invalid role {role!r}")
        if not isinstance(content, str) or not content.strip():
            errors.append(f"{name}:{i}: messages[{j}] needs non-empty string content")
        roles.append(role)

    if roles[-1] != "assistant":
        errors.append(f"{name}:{i}: last message must be role=assistant (the judgment)")
    if "user" not in roles:
        errors.append(f"{name}:{i}: needs a user message (the thing to judge)")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        default=[Path("data/train.jsonl"), Path("data/valid.jsonl")],
    )
    args = parser.parse_args()

    all_errors: list[str] = []
    total = 0
    for path in args.paths:
        if not path.exists():
            all_errors.append(f"{path}: file not found")
            continue
        rows, errors = load_jsonl(path)
        total += len(rows)
        print(f"{path}: {len(rows)} examples, {len(errors)} issues")
        all_errors.extend(errors)

    if all_errors:
        print("\nIssues:")
        for err in all_errors:
            print(f"  - {err}")
        return 1

    print(f"OK: {total} examples")
    return 0


if __name__ == "__main__":
    sys.exit(main())
