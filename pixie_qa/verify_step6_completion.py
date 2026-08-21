"""验证 Pixie Step 6 的 pending evaluator 与必需分析交付物。"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        raise ValueError("usage: verify_step6_completion.py RESULT_DIR")
    result = Path(sys.argv[1])
    required = ("dataset-0/analysis.md", "dataset-0/analysis-summary.md", "action-plan.md", "action-plan-summary.md")
    missing = [name for name in required if not (result / name).is_file()]
    evaluations = list(result.glob("dataset-*/entry-*/evaluations.jsonl"))
    if not evaluations:
        raise ValueError("no evaluation files")
    pending: list[str] = []
    for path in evaluations:
        for line in path.read_text(encoding="utf-8").splitlines():
            value = json.loads(line)
            if value.get("status") == "pending" or not isinstance(value.get("score"), float):
                pending.append(str(path))
    if missing or pending:
        raise ValueError(f"missing={missing}, pending={pending}")
    print("Step 6 completion check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
