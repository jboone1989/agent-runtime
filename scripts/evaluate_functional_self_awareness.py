"""Run the frozen functional-self-awareness acceptance benchmark."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent_runtime.awareness import run_benchmark


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--cycles", type=int, default=100)
    args = parser.parse_args()
    report = run_benchmark(
        args.output_dir.resolve(),
        independent_trials=args.trials,
        cycles_per_trial=args.cycles,
    )
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    return 0 if report.accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())
