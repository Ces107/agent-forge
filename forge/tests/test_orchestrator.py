"""Tests for the pipeline orchestrator's pure logic and gates (no live CLI invoked)."""

from __future__ import annotations

from pathlib import Path

import pytest

from forge import orchestrator
from forge.orchestrator import (
    GateError,
    Stage,
    StageResult,
    build_plan,
    format_plan,
    gate_review,
    gate_task_coverage,
    gate_traceability,
    gate_verify,
    main,
)

_PASS_SPEC = "AC-1 the system shall close its books.\n"
_PASS_TASKS = "## T-1 close books\ncovers: AC-1\nverified_by: test_books_close\n"


def test_plan_is_six_ordered_stages() -> None:
    plan = build_plan()
    assert [step.stage for step in plan] == [
        Stage.SPEC,
        Stage.ARCHITECT,
        Stage.TASKS,
        Stage.IMPLEMENT,
        Stage.REVIEW,
        Stage.VERIFY,
    ]


def test_tasks_stage_maps_to_planner_agent() -> None:
    assert Stage.TASKS.value == "planner"


def test_every_stage_has_a_provenance_identity() -> None:
    assert set(orchestrator.PROVENANCE) == set(Stage)
    assert all(ident.endswith("@agent-forge.bot") for ident in orchestrator.PROVENANCE.values())


def test_format_plan_lists_models() -> None:
    out = format_plan(build_plan())
    assert "SPEC" in out
    assert "model=opus" in out
    assert "model=haiku" in out


def test_main_dry_run_prints_plan(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    mandate = tmp_path / "mandate.md"
    mandate.write_text("build a thing", encoding="utf-8")
    assert main(["--mandate", str(mandate), "--dry-run"]) == 0
    assert "ARCHITECT" in capsys.readouterr().out


def test_gate_verify_passes_on_marker() -> None:
    gate_verify(StageResult(Stage.VERIFY, "all green\nVERIFY: PASS\n", 0))


def test_gate_verify_halts_without_marker() -> None:
    with pytest.raises(GateError, match="VERIFY: PASS not present"):
        gate_verify(StageResult(Stage.VERIFY, "checks failed", 1))


def test_gate_review_halts_when_no_review_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(orchestrator, "REVIEW_DIR", tmp_path)
    with pytest.raises(GateError, match="no REVIEW"):
        gate_review()


def test_gate_review_halts_without_verdict_line(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "REVIEW-001.md").write_text("looks fine to me", encoding="utf-8")
    monkeypatch.setattr(orchestrator, "REVIEW_DIR", tmp_path)
    with pytest.raises(GateError, match="no GATE verdict"):
        gate_review()


def test_gate_review_passes_with_verdict(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "REVIEW-001.md").write_text("GATE: FAIL — found a bug", encoding="utf-8")
    monkeypatch.setattr(orchestrator, "REVIEW_DIR", tmp_path)
    gate_review()


def _wire_trace(monkeypatch: pytest.MonkeyPatch, work: Path, tests: Path) -> None:
    monkeypatch.setattr(orchestrator, "WORK_DIR", work)
    monkeypatch.setattr(orchestrator, "TESTS_DIR", tests)


def test_gate_traceability_passes_on_full_coverage(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    work = tmp_path / "work"
    tests = tmp_path / "tests"
    work.mkdir()
    tests.mkdir()
    (work / "spec.md").write_text(_PASS_SPEC, encoding="utf-8")
    (work / "tasks.md").write_text(_PASS_TASKS, encoding="utf-8")
    (tests / "test_x.py").write_text("def test_books_close(): ...", encoding="utf-8")
    _wire_trace(monkeypatch, work, tests)
    gate_traceability()


def test_gate_traceability_halts_on_uncovered_ac(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    work = tmp_path / "work"
    tests = tmp_path / "tests"
    work.mkdir()
    tests.mkdir()
    (work / "spec.md").write_text("AC-1 x\nAC-2 uncovered\n", encoding="utf-8")
    (work / "tasks.md").write_text(_PASS_TASKS, encoding="utf-8")
    (tests / "test_x.py").write_text("def test_books_close(): ...", encoding="utf-8")
    _wire_trace(monkeypatch, work, tests)
    with pytest.raises(GateError, match="traceability gate"):
        gate_traceability()


def test_gate_task_coverage_passes_before_tests(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # The Tasks-stage gate checks spec<->task coverage only; tests do not exist yet (empty dir).
    work = tmp_path / "work"
    tests = tmp_path / "tests"
    work.mkdir()
    tests.mkdir()
    (work / "spec.md").write_text(_PASS_SPEC, encoding="utf-8")
    (work / "tasks.md").write_text(_PASS_TASKS, encoding="utf-8")
    _wire_trace(monkeypatch, work, tests)
    gate_task_coverage()


def test_gate_task_coverage_halts_on_uncovered_ac(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    work = tmp_path / "work"
    tests = tmp_path / "tests"
    work.mkdir()
    tests.mkdir()
    (work / "spec.md").write_text("AC-1 x\nAC-2 uncovered\n", encoding="utf-8")
    (work / "tasks.md").write_text(_PASS_TASKS, encoding="utf-8")
    _wire_trace(monkeypatch, work, tests)
    with pytest.raises(GateError, match="uncovered AC: AC-2"):
        gate_task_coverage()


def test_gate_task_coverage_halts_on_orphan_task_ref(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    work = tmp_path / "work"
    tests = tmp_path / "tests"
    work.mkdir()
    tests.mkdir()
    (work / "spec.md").write_text("AC-1 x\n", encoding="utf-8")
    (work / "tasks.md").write_text("## T-1 x\ncovers: AC-1, AC-7\nverified_by: test_a\n", encoding="utf-8")
    _wire_trace(monkeypatch, work, tests)
    with pytest.raises(GateError, match="orphan task refs: AC-7"):
        gate_task_coverage()
