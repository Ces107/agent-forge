"""Tests for the spec<->task<->test traceability gate."""

from __future__ import annotations

from pathlib import Path

import pytest

from forge.traceability import (
    build_report,
    collect_test_corpus,
    main,
    parse_acceptance_criteria,
    parse_tasks,
    run,
)

_SPEC = """
# Spec
AC-1 The system shall write a matching debit and credit for every transfer.
AC-2 When a duplicate idempotency_key arrives, the system shall return the original result.
AC-3 The system shall never drive a balance below the floor.
"""

_TASKS = """
# Tasks
## T-1 Double-entry posting
covers: AC-1
verified_by: test_books_close

## T-2 Idempotency replay
covers: AC-2
verified_by: test_duplicate_key, test_replay_across_restart

## T-3 Overdraft floor
covers: AC-3
verified_by: test_overdraft_blocked
"""

_TEST_CORPUS = """
def test_books_close(): ...
def test_duplicate_key(): ...
def test_replay_across_restart(): ...
def test_overdraft_blocked(): ...
"""


def test_parse_acceptance_criteria_dedupes_and_sorts() -> None:
    assert parse_acceptance_criteria("AC-3 AC-1 AC-1 AC-2") == ("AC-1", "AC-2", "AC-3")


def test_parse_tasks_isolates_attributes_per_block() -> None:
    tasks = parse_tasks(_TASKS)
    assert [t.task_id for t in tasks] == ["T-1", "T-2", "T-3"]
    assert tasks[1].covers == ("AC-2",)
    assert tasks[1].verified_by == ("test_duplicate_key", "test_replay_across_restart")


def test_clean_report_passes() -> None:
    report = build_report(_SPEC, _TASKS, _TEST_CORPUS)
    assert report.ok
    assert "TRACE: PASS" in report.render()


def test_uncovered_criterion_fails() -> None:
    spec = _SPEC + "\nAC-4 The system shall expose a reconciliation proof.\n"
    report = build_report(spec, _TASKS, _TEST_CORPUS)
    assert not report.ok
    assert report.uncovered_criteria == ("AC-4",)
    assert "UNCOVERED" in report.render()


def test_orphan_task_reference_fails() -> None:
    tasks = _TASKS + "\n## T-4 Phantom\ncovers: AC-9\nverified_by: test_books_close\n"
    report = build_report(_SPEC, tasks, _TEST_CORPUS)
    assert not report.ok
    assert report.orphan_task_refs == ("AC-9",)


def test_unverified_task_fails_when_test_missing() -> None:
    report = build_report(_SPEC, _TASKS, "def test_books_close(): ...")
    assert not report.ok
    assert "T-2" in report.unverified_tasks
    assert "T-3" in report.unverified_tasks


def test_structure_ok_ignores_missing_tests() -> None:
    # Greenfield: tasks cover every AC but no test exists yet. Structure passes; full check fails.
    report = build_report(_SPEC, _TASKS, "")
    assert report.structure_ok
    assert not report.ok


def test_structure_fails_on_uncovered_criterion() -> None:
    spec = _SPEC + "\nAC-9 uncovered requirement.\n"
    report = build_report(spec, _TASKS, _TEST_CORPUS)
    assert not report.structure_ok


def test_selector_matches_whole_identifier_not_substring() -> None:
    # 'test_books_close' must NOT be considered present just because a longer name contains it.
    corpus = "def test_books_close_extended(): ..."
    report = build_report("AC-1 x", "## T-1 x\ncovers: AC-1\nverified_by: test_books_close\n", corpus)
    assert "T-1" in report.unverified_tasks


def test_task_without_verified_by_is_unverified() -> None:
    tasks = "## T-1 No test\ncovers: AC-1\n"
    report = build_report("AC-1 thing", tasks, "anything")
    assert report.unverified_tasks == ("T-1",)


def test_collect_test_corpus_reads_nested_test_files(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    (tmp_path / "test_a.py").write_text("def test_alpha(): ...", encoding="utf-8")
    (tmp_path / "sub" / "test_b.py").write_text("def test_beta(): ...", encoding="utf-8")
    (tmp_path / "helper.py").write_text("def not_a_test(): ...", encoding="utf-8")
    corpus = collect_test_corpus(tmp_path)
    assert "test_alpha" in corpus
    assert "test_beta" in corpus
    assert "not_a_test" not in corpus


def test_collect_test_corpus_handles_missing_dir(tmp_path: Path) -> None:
    assert collect_test_corpus(tmp_path / "nope") == ""


def test_run_end_to_end_on_disk(tmp_path: Path) -> None:
    spec = tmp_path / "spec.md"
    tasks = tmp_path / "tasks.md"
    tests = tmp_path / "tests"
    tests.mkdir()
    spec.write_text(_SPEC, encoding="utf-8")
    tasks.write_text(_TASKS, encoding="utf-8")
    (tests / "test_ledger.py").write_text(_TEST_CORPUS, encoding="utf-8")

    report = run(spec, tasks, tests)
    assert report.ok


def test_cli_main_returns_zero_on_pass(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    spec = tmp_path / "spec.md"
    tasks = tmp_path / "tasks.md"
    tests = tmp_path / "tests"
    tests.mkdir()
    spec.write_text(_SPEC, encoding="utf-8")
    tasks.write_text(_TASKS, encoding="utf-8")
    (tests / "test_ledger.py").write_text(_TEST_CORPUS, encoding="utf-8")

    code = main(["--spec", str(spec), "--tasks", str(tasks), "--tests-dir", str(tests)])
    assert code == 0
    assert "TRACE: PASS" in capsys.readouterr().out


def test_cli_main_returns_one_on_fail(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    spec = tmp_path / "spec.md"
    tasks = tmp_path / "tasks.md"
    tests = tmp_path / "tests"
    tests.mkdir()
    spec.write_text("AC-1 x\nAC-2 uncovered\n", encoding="utf-8")
    tasks.write_text(_TASKS, encoding="utf-8")
    (tests / "test_ledger.py").write_text(_TEST_CORPUS, encoding="utf-8")

    code = main(["--spec", str(spec), "--tasks", str(tasks), "--tests-dir", str(tests)])
    assert code == 1
    assert "TRACE: FAIL" in capsys.readouterr().out
