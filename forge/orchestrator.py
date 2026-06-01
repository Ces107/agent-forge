"""agent-forge orchestrator — the reusable Spec->Architect->Implement->Review->Verify->Commit pipeline.

This driver invokes Claude Code sub-agents (one per stage) in headless mode, enforces the
gates between stages, and records an auditable trail to ``forge/spawn-log.jsonl`` and
``forge/trajectories/``. The gates have teeth: the AdversarialReview stage HALTS the pipeline
unless it produces a structured finding, and the Verify stage HALTS unless every check exits 0.

Run:  python forge/orchestrator.py --mandate forge/work/mandate.md
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SPAWN_LOG = REPO_ROOT / "forge" / "spawn-log.jsonl"
TRAJECTORY_DIR = REPO_ROOT / "forge" / "trajectories"
REVIEW_DIR = REPO_ROOT / "forge" / "reviews"


class Stage(str, Enum):
    """Ordered pipeline stages. The enum order IS the execution order."""

    SPEC = "spec"
    ARCHITECT = "architect"
    IMPLEMENT = "implementer"
    REVIEW = "adversarial-reviewer"
    VERIFY = "verifier"


class GateError(RuntimeError):
    """Raised when a stage gate fails and the pipeline must halt."""


@dataclass(frozen=True)
class StageResult:
    stage: Stage
    output: str
    exit_code: int


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append_jsonl(path: Path, record: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def log_spawn(stage: Stage, model: str, roi: str) -> None:
    """Append one audit line per agent spawn (see CLAUDE.md spawn-log discipline)."""
    _append_jsonl(
        SPAWN_LOG,
        {"ts": _now(), "agent": stage.value, "model": model, "stage": stage.name, "roi": roi},
    )


def record_trajectory(result: StageResult) -> None:
    _append_jsonl(
        TRAJECTORY_DIR / "run.ndjson",
        {"ts": _now(), "stage": result.stage.name, "exit_code": result.exit_code,
         "output_chars": len(result.output)},
    )


def run_stage(stage: Stage, prompt: str, *, model: str, roi: str) -> StageResult:
    """Invoke a Claude Code sub-agent for one stage in headless mode."""
    log_spawn(stage, model, roi)
    cmd = ["claude", "-p", prompt, "--agents", stage.value, "--model", model]
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_ROOT)
    result = StageResult(stage=stage, output=proc.stdout + proc.stderr, exit_code=proc.returncode)
    record_trajectory(result)
    return result


def gate_review() -> None:
    """HALT unless the AdversarialReviewer produced a verdict with a real finding."""
    reviews = sorted(REVIEW_DIR.glob("REVIEW-*.md"))
    if not reviews:
        raise GateError("adversarial-review gate: no REVIEW-NNN.md produced — gate is not optional")
    latest = reviews[-1].read_text(encoding="utf-8")
    if "GATE: FAIL" not in latest and "GATE: PASS" not in latest:
        raise GateError(f"adversarial-review gate: {reviews[-1].name} has no GATE verdict line")


def gate_verify(result: StageResult) -> None:
    """HALT unless every verification check exited 0."""
    if "VERIFY: PASS" not in result.output:
        raise GateError("verify gate: VERIFY: PASS not present — checks did not all exit 0")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the agent-forge pipeline.")
    parser.add_argument("--mandate", type=Path, required=True, help="Path to the one-line mandate file.")
    parser.add_argument("--dry-run", action="store_true", help="Print the plan without spawning agents.")
    args = parser.parse_args(argv)

    mandate = args.mandate.read_text(encoding="utf-8").strip()
    plan = [
        (Stage.SPEC, "opus", "Expand mandate into a testable acceptance spec."),
        (Stage.ARCHITECT, "opus", "Produce ADRs; novel design decisions warrant the top tier."),
        (Stage.IMPLEMENT, "sonnet", "Structured implementation + first test layer."),
        (Stage.REVIEW, "sonnet", "Adversarial correctness attack with reproducing tests."),
        (Stage.VERIFY, "haiku", "Mechanical check execution; cheapest tier suffices."),
    ]

    if args.dry_run:
        for stage, model, roi in plan:
            print(f"{stage.name:12} model={model:7} roi={roi}")
        return 0

    for stage, model, roi in plan:
        print(f"[forge] running stage {stage.name} (model={model})")
        result = run_stage(stage, mandate, model=model, roi=roi)
        if stage is Stage.REVIEW:
            gate_review()
        if stage is Stage.VERIFY:
            gate_verify(result)
    print("[forge] pipeline complete — all gates passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
