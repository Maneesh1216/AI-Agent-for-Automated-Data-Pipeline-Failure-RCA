#!/usr/bin/env python3
"""Score the agent against the labelled fixtures."""

import json
import signal

# Piping to `head` closes stdout early; without this Python raises
# BrokenPipeError and prints a traceback that looks like a crash.
try:
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
except (AttributeError, ValueError):  # not available on Windows
    pass

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rca_agent.evaluation import run_evaluation
from rca_agent.graph import RCAAgent
from rca_agent.tracking import Tracker


def main() -> int:
    agent = RCAAgent()
    report = run_evaluation(agent)
    summary = report["summary"]

    with Tracker(run_name=f"eval-{agent.runtime}") as tracker:
        tracker.log_params({"runtime": agent.runtime, "narrator": agent.llm.name})
        tracker.log_metrics({k: v for k, v in summary.items() if isinstance(v, (int, float))})

    print(json.dumps(summary, indent=2))
    wrong = [r for r in report["results"] if not r["correct"]]
    if wrong:
        print("\nMisclassified:")
        for r in wrong:
            print(f"  {r['fixture']}: expected {r['expected']}, got {r['predicted']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
