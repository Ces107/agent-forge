"""Un-blind a judge's scores and compute the benchmark table.

Usage:  python bench/code-review/score.py <judge-dir> [--labels A=proven-code-review,B=baseline]

Reads <judge-dir>/scores.json (per case, per anonymous review X/Y) and <judge-dir>/mapping.json
(which contestant was X and Y), and prints one markdown table row per contestant.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _answer_keys() -> dict[str, dict]:
    keys: dict[str, dict] = {}
    for name in ("answers.json", "answers_holdout.json"):
        data = json.loads((HERE / name).read_text(encoding="utf-8"))
        keys.update({k: v for k, v in data.items() if not k.startswith("_")})
    return keys


def main(argv: list[str]) -> int:
    judge = Path(argv[0])
    labels = {"A": "A", "B": "B"}
    if "--labels" in argv:
        for pair in argv[argv.index("--labels") + 1].split(","):
            who, name = pair.split("=", 1)
            labels[who] = name
    scores = json.loads((judge / "scores.json").read_text(encoding="utf-8"))
    mapping = json.loads((judge / "mapping.json").read_text(encoding="utf-8"))
    keys = _answer_keys()

    totals: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for case_id, per_review in scores.items():
        if case_id.startswith("_"):
            continue  # judge notes, not a case
        seeded = {d["id"] for d in keys[case_id]["defects"]}
        clean = not seeded
        for anon, s in per_review.items():
            who = mapping[case_id][anon]
            t = totals[who]
            t["cases"] += 1
            t["seeded_total"] += len(seeded)
            t["seeded_found"] += len(set(s.get("seeded_found", [])) & seeded)
            t["findings"] += s.get("findings_total", 0)
            t["real"] += s.get("real_findings", 0)
            t["false_positives"] += len(s.get("false_positives", []))
            t["nits"] += s.get("nits_or_suggestions", 0)
            t["proven"] += s.get("evidence_executed", 0)
            t["verdict_ok"] += 1 if s.get("verdict_correct") else 0
            t["severity_ok"] += 1 if s.get("severity_calibrated", True) or clean else 0
            if clean:
                t["clean_cases"] += 1
                t["clean_false_alarms"] += len(s.get("false_positives", []))

    print("| Reviewer | Seeded bugs found | Precision (real / reported) | False positives | Nits | "
          "Real findings proven by a run | Correct verdicts | False alarms on clean PRs |")
    print("|---|---|---|---|---|---|---|---|")
    for who in sorted(totals):
        t = totals[who]
        precision = t["real"] / t["findings"] if t["findings"] else 1.0
        proof = t["proven"] / t["real"] if t["real"] else 0.0
        print(
            f"| {labels.get(who, who)} | {int(t['seeded_found'])}/{int(t['seeded_total'])} | "
            f"{precision:.0%} ({int(t['real'])}/{int(t['findings'])}) | {int(t['false_positives'])} | "
            f"{int(t['nits'])} | {proof:.0%} ({int(t['proven'])}/{int(t['real'])}) | "
            f"{int(t['verdict_ok'])}/{int(t['cases'])} | "
            f"{int(t['clean_false_alarms'])} in {int(t['clean_cases'])} |"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
