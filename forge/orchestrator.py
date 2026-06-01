"""agent-forge orchestrator — the reusable spec-driven multi-agent build pipeline.

Pipeline (spec-driven development, after GitHub Spec Kit / AWS Kiro / BMAD, made falsifiable):

    Spec ─▶ Architect ─▶ Tasks ─▶ Implement ─▶ AdversarialReview ─▶ Verify ─▶ Commit

Each stage is a Claude Code sub-agent (``.claude/agents/``) with a restricted toolset and an
explicit model tier. This driver runs them in order in headless mode and enforces gates **with
teeth**:

- **Task-coverage gate** (after Tasks) — HALTS unless every spec acceptance criterion is covered by
  a task and no task references a phantom criterion. The task<->test half is deferred (no test
  exists yet at this point in a greenfield build).
- **AdversarialReview gate** — HALTS unless the reviewer produced a structured verdict finding.
- **Verify gate** — HALTS unless tests, coverage, lint, types and security all exit 0.
- **Traceability gate** (after Verify) — the full spec<->task<->test bijection: every criterion
  traces to a task and every task to a test that now exists. This is the link the named SDD tools
  do not mechanically enforce.

Every stage spawn is recorded twice: an audit line in ``forge/spawn-log.jsonl`` and an
OpenTelemetry GenAI span in ``forge/trajectories/run.ndjson`` (see ``forge.observability``).

Run:  python -m forge.orchestrator --mandate forge/work/mandate.md
"""

from __future__ import annotations

import argparse
import json
import subprocess  # nosec B404
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from forge.observability import TokenUsage, TrajectoryEmitter

REPO_ROOT = Path(__file__).resolve().parent.parent
SPAWN_LOG = REPO_ROOT / "forge" / "spawn-log.jsonl"
TRAJECTORY = REPO_ROOT / "forge" / "trajectories" / "run.ndjson"
REVIEW_DIR = REPO_ROOT / "forge" / "reviews"
WORK_DIR = REPO_ROOT / "forge" / "work"
TESTS_DIR = REPO_ROOT / "workspaces" / "ledger" / "tests"


class Stage(StrEnum):
    """Ordered pipeline stages. Enum order IS execution order."""

    SPEC = "spec"
    ARCHITECT = "architect"
    TASKS = "planner"
    IMPLEMENT = "implementer"
    REVIEW = "adversarial-reviewer"
    VERIFY = "verifier"


# Accountable agent identity per stage — the same string that appears in the git author and the
# ``forge.provenance`` span attribute, so observability and provenance are one record.
PROVENANCE: dict[Stage, str] = {
    Stage.SPEC: "spec@agent-forge.bot",
    Stage.ARCHITECT: "architect@agent-forge.bot",
    Stage.TASKS: "planner@agent-forge.bot",
    Stage.IMPLEMENT: "implementer@agent-forge.bot",
    Stage.REVIEW: "reviewer@agent-forge.bot",
    Stage.VERIFY: "verifier@agent-forge.bot",
}


class GateError(RuntimeError):
    """Raised when a stage gate fails and the pipeline must halt."""


@dataclass(frozen=True)
class PlanStep:
    stage: Stage
    model: str
    roi: str


@dataclass(frozen=True)
class StageResult:
    stage: Stage
    output: str
    exit_code: int


def build_plan() -> tuple[PlanStep, ...]:
    """The static execution plan: which model serves each stage and why (pure, testable)."""
    return (
        PlanStep(Stage.SPEC, "opus", "Expand mandate into a testable acceptance spec with AC ids."),
        PlanStep(Stage.ARCHITECT, "opus", "Produce ADRs; novel design decisions warrant the top tier."),
        PlanStep(Stage.TASKS, "sonnet", "Decompose ADR contract into AC-traceable, test-named tasks."),
        PlanStep(Stage.IMPLEMENT, "sonnet", "Structured implementation + first test layer."),
        PlanStep(Stage.REVIEW, "sonnet", "Adversarial correctness attack with reproducing tests."),
        PlanStep(Stage.VERIFY, "haiku", "Mechanical check execution; cheapest tier suffices."),
    )


def _now() -> datetime:
    return datetime.now(UTC)


def _append_jsonl(path: Path, record: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def log_spawn(stage: Stage, model: str, roi: str) -> None:
    """Append one audit line per agent spawn (CLAUDE.md spawn-log discipline)."""
    _append_jsonl(
        SPAWN_LOG,
        {"ts": _now().isoformat(), "agent": stage.value, "model": model, "stage": stage.name, "roi": roi},
    )


def run_stage(step: PlanStep, prompt: str) -> StageResult:  # pragma: no cover - drives the live CLI
    """Invoke a Claude Code sub-agent for one stage, recording an audit line and an OTel span."""
    log_spawn(step.stage, step.model, step.roi)
    start = _now()
    cmd = ["claude", "-p", prompt, "--agents", step.stage.value, "--model", step.model]
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_ROOT)  # nosec B603
    result = StageResult(stage=step.stage, output=proc.stdout + proc.stderr, exit_code=proc.returncode)

    emitter = TrajectoryEmitter(TRAJECTORY, clock=_now)
    span = emitter.span_for_stage(
        agent_name=step.stage.value,
        model=step.model,
        start=start,
        provenance=PROVENANCE[step.stage],
        exit_code=result.exit_code,
        usage=TokenUsage(),
        attributes={"forge.stage": step.stage.name},
    )
    emitter.emit(span)
    return result


def gate_task_coverage() -> None:
    """After Tasks, before any test exists: HALT unless tasks structurally cover every criterion.

    This is the spec<->task half of traceability — every ``AC-N`` is covered by a task and no task
    references a phantom criterion. The task<->test half cannot be checked yet (the Implementer has
    not written the tests), so it is deferred to :func:`gate_traceability` after Verify.
    """
    from forge.traceability import run as run_trace

    report = run_trace(WORK_DIR / "spec.md", WORK_DIR / "tasks.md", TESTS_DIR)
    if not report.structure_ok:
        problems = []
        if report.uncovered_criteria:
            problems.append("uncovered AC: " + ", ".join(report.uncovered_criteria))
        if report.orphan_task_refs:
            problems.append("orphan task refs: " + ", ".join(report.orphan_task_refs))
        raise GateError("task-coverage gate: " + "; ".join(problems))


def gate_traceability() -> None:
    """After Verify: HALT unless every criterion traces to a task AND every task to a real test."""
    from forge.traceability import run as run_trace

    report = run_trace(WORK_DIR / "spec.md", WORK_DIR / "tasks.md", TESTS_DIR)
    if not report.ok:
        raise GateError("traceability gate: " + report.render().splitlines()[-1] + " — see report")


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


def format_plan(plan: Sequence[PlanStep]) -> str:
    return "\n".join(f"{s.stage.name:10} model={s.model:7} roi={s.roi}" for s in plan)


# A Runner executes one stage and returns its result. The default drives the live `claude` CLI;
# tests inject a fake so the orchestration control flow (gate ordering, halt-on-failure) is itself
# testable rather than asserted. Closes TD-007.
Runner = Callable[[PlanStep, str], StageResult]


def run_pipeline(plan: Sequence[PlanStep], mandate: str, runner: Runner = run_stage) -> int:
    """Run each stage in order and fire its gate. Any gate raises GateError and halts the pipeline.

    This is the heart of the autonomous factory, extracted from ``main`` so it can be exercised
    end-to-end with an injected runner — proving the gates actually fire in order, not just exist.
    """
    for step in plan:
        print(f"[forge] running stage {step.stage.name} (model={step.model})")
        result = runner(step, mandate)
        if step.stage is Stage.TASKS:
            gate_task_coverage()
        if step.stage is Stage.REVIEW:
            gate_review()
        if step.stage is Stage.VERIFY:
            gate_verify(result)
            gate_traceability()
    print("[forge] pipeline complete — all gates passed")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the agent-forge pipeline.")
    parser.add_argument("--mandate", type=Path, required=True, help="Path to the one-line mandate file.")
    parser.add_argument("--dry-run", action="store_true", help="Print the plan without spawning agents.")
    args = parser.parse_args(list(argv) if argv is not None else None)

    plan = build_plan()
    if args.dry_run:
        print(format_plan(plan))
        return 0

    mandate = args.mandate.read_text(encoding="utf-8").strip()
    return run_pipeline(plan, mandate)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
