"""The eval harness itself must be trustworthy: with perfect extraction, the rules should catch every planted issue."""
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "evals"))

import generate  # noqa: E402
import run_evals  # noqa: E402


def test_oracle_run_catches_everything():
    rng = random.Random(1)
    kinds = list(generate.ISSUES)
    cases = [json.loads(json.dumps(generate.make_case(i, kinds[i % len(kinds)], rng))) for i in range(60)]
    report = run_evals.run(cases, mock=True, model=None)
    assert report["catch_rate_overall"] == 1.0
    assert report["false_alarm_rate"] == 0.0
    assert report["decision_accuracy"] == 1.0
