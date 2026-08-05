#!/usr/bin/env python3
"""Diagnose an incident from the command line."""

import signal

# Piping to `head` closes stdout early; without this Python raises
# BrokenPipeError and prints a traceback that looks like a crash.
try:
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
except (AttributeError, ValueError):  # not available on Windows
    pass

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rca_agent.graph import RCAAgent
from rca_agent.loader import list_incidents, load_incident
from rca_agent.report import render_markdown


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose a pipeline failure")
    parser.add_argument("incident", nargs="?", help="path to an incident directory")
    parser.add_argument("--all", action="store_true", help="diagnose every fixture")
    parser.add_argument("--markdown", action="store_true", help="full incident report")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    agent = RCAAgent()
    targets = list_incidents() if args.all or not args.incident else [Path(args.incident)]

    for target in targets:
        incident = load_incident(target)
        diagnosis = agent.diagnose(incident)

        if args.json:
            import json
            print(json.dumps(diagnosis.to_dict(), indent=2, default=str))
            continue
        if args.markdown:
            print(render_markdown(diagnosis))
            print("\n" + "=" * 70 + "\n")
            continue

        cls = diagnosis.classification
        print(f"\n{'='*70}")
        print(f"{incident.dag_id}.{incident.failed_task}   [{target.name}]")
        print(f"{'='*70}")
        print(f"  class      : {cls.failure_class.value}  ({cls.confidence:.0%})")
        print(f"  severity   : {diagnosis.severity.value}")
        print(f"  signals    : {', '.join(cls.matched_signals) or '(none)'}")
        print(f"  runtime    : {agent.runtime}  narrator={diagnosis.generated_by}  {diagnosis.latency_ms}ms")
        if cls.rationale:
            print(f"  rationale  : {cls.rationale}")
        print(f"\n  {diagnosis.root_cause}\n")
        print("  Next steps:")
        for i, step in enumerate(diagnosis.remediation[:3], 1):
            print(f"    {i}. {step}")
        if diagnosis.similar_incidents:
            top = diagnosis.similar_incidents[0]
            print(f"\n  Closest past incident: {top['id']} ({top['score']}) — {top['resolution'][:100]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
