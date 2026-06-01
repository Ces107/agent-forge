"""Spec <-> Task <-> Test traceability gate — the falsifiable core of spec-driven development.

Why this exists
---------------
Spec-driven development (GitHub Spec Kit, AWS Kiro, BMAD-METHOD) is the 2026 answer to "vibe
coding": the spec is the source of truth and code is a regenerable output of it. Those tools give
you the *workflow* (Specify -> Plan -> Tasks -> Implement) but they do **not** mechanically prove
that the implementation actually closed over the spec. Drift is caught by human review, if at all.

This module makes that proof a machine check. Given:

- a **spec** with EARS-style acceptance criteria tagged ``AC-N``,
- a **tasks** file where each task ``T-N`` declares ``covers: AC-x[, AC-y]`` and
  ``verified_by: <test-selector substring>``,
- the **test directory** of the inner project,

it asserts three coverage properties and fails (exit non-zero) on any violation:

1. **No uncovered acceptance criterion** — every ``AC-N`` in the spec is covered by >=1 task.
2. **No orphan task** — every task's ``covers:`` references an AC that actually exists.
3. **No unverified task** — every task's ``verified_by:`` substring is present in some test file,
   i.e. the claimed test actually exists.

Together these give an end-to-end, ``git``-checkable chain: every requirement reaches a test, no
requirement is silently dropped, and no task points at a test that was never written. None of the
named SDD tools enforce this bijection in CI; this is where agent-forge goes beyond them.

Usage:  python -m forge.traceability \
            --spec forge/work/spec.md \
            --tasks forge/work/tasks.md \
            --tests-dir workspaces/ledger/tests
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

AC_PATTERN = re.compile(r"\bAC-(\d+)\b")
TASK_HEADER_PATTERN = re.compile(r"\bT-(\d+)\b")
COVERS_PATTERN = re.compile(r"covers:\s*(?P<refs>[^\n]+)", re.IGNORECASE)
VERIFIED_BY_PATTERN = re.compile(r"verified_by:\s*(?P<sel>[^\n]+)", re.IGNORECASE)


@dataclass(frozen=True)
class Task:
    """One implementation task with its backward (AC) and forward (test) trace links."""

    task_id: str
    covers: tuple[str, ...]
    verified_by: tuple[str, ...]


@dataclass(frozen=True)
class TraceReport:
    """The outcome of a traceability check. ``ok`` is the gate verdict."""

    acceptance_criteria: tuple[str, ...]
    tasks: tuple[Task, ...]
    uncovered_criteria: tuple[str, ...]
    orphan_task_refs: tuple[str, ...]
    unverified_tasks: tuple[str, ...]

    @property
    def structure_ok(self) -> bool:
        """Spec<->task coverage only (no test dependency).

        Checkable right after the Tasks stage, before any test exists: every acceptance criterion
        is covered by a task and no task references a non-existent criterion.
        """
        return not (self.uncovered_criteria or self.orphan_task_refs)

    @property
    def ok(self) -> bool:
        """Full end-to-end verdict: structure is sound AND every task names a real test.

        Checkable only after the Implement/Verify stages, once the named tests exist.
        """
        return self.structure_ok and not self.unverified_tasks

    def render(self) -> str:
        lines = [
            "# Traceability report (spec <-> task <-> test)",
            "",
            f"- acceptance criteria: {len(self.acceptance_criteria)} "
            f"({', '.join(self.acceptance_criteria) or 'none'})",
            f"- tasks: {len(self.tasks)}",
        ]
        if self.uncovered_criteria:
            lines.append(f"- UNCOVERED acceptance criteria: {', '.join(self.uncovered_criteria)}")
        if self.orphan_task_refs:
            lines.append(f"- ORPHAN task references (no such AC): {', '.join(self.orphan_task_refs)}")
        if self.unverified_tasks:
            lines.append(f"- UNVERIFIED tasks (test selector not found): {', '.join(self.unverified_tasks)}")
        verdict = "TRACE: PASS" if self.ok else "TRACE: FAIL"
        lines.extend(["", verdict])
        return "\n".join(lines)


def parse_acceptance_criteria(spec_text: str) -> tuple[str, ...]:
    """Return the sorted, de-duplicated set of ``AC-N`` ids declared in the spec."""
    found = {f"AC-{m.group(1)}" for m in AC_PATTERN.finditer(spec_text)}
    return tuple(sorted(found, key=_numeric_suffix))


def parse_tasks(tasks_text: str) -> tuple[Task, ...]:
    """Parse ``T-N`` blocks, each with its ``covers:`` and ``verified_by:`` lines.

    A task block runs from one ``T-N`` header to the next (or end of file). ``covers`` and
    ``verified_by`` are read from within that block, so attributes never bleed across tasks.
    """
    headers = list(TASK_HEADER_PATTERN.finditer(tasks_text))
    tasks: list[Task] = []
    for index, match in enumerate(headers):
        start = match.start()
        end = headers[index + 1].start() if index + 1 < len(headers) else len(tasks_text)
        block = tasks_text[start:end]
        task_id = f"T-{match.group(1)}"
        covers = _extract_refs(block, COVERS_PATTERN, AC_PATTERN)
        verified_by = _extract_selectors(block)
        tasks.append(Task(task_id=task_id, covers=covers, verified_by=verified_by))
    return tuple(tasks)


def _extract_refs(block: str, line_pattern: re.Pattern[str], ref_pattern: re.Pattern[str]) -> tuple[str, ...]:
    line_match = line_pattern.search(block)
    if line_match is None:
        return ()
    refs = {f"AC-{m.group(1)}" for m in ref_pattern.finditer(line_match.group("refs"))}
    return tuple(sorted(refs, key=_numeric_suffix))


def _extract_selectors(block: str) -> tuple[str, ...]:
    line_match = VERIFIED_BY_PATTERN.search(block)
    if line_match is None:
        return ()
    raw = line_match.group("sel")
    selectors = [part.strip() for part in raw.split(",")]
    return tuple(sel for sel in selectors if sel)


def collect_test_corpus(tests_dir: Path) -> str:
    """Concatenate every ``test_*.py`` body so ``verified_by`` substrings can be searched once."""
    if not tests_dir.exists():
        return ""
    parts = [path.read_text(encoding="utf-8") for path in sorted(tests_dir.rglob("test_*.py"))]
    return "\n".join(parts)


def build_report(spec_text: str, tasks_text: str, test_corpus: str) -> TraceReport:
    criteria = parse_acceptance_criteria(spec_text)
    tasks = parse_tasks(tasks_text)
    criteria_set = set(criteria)

    covered = {ac for task in tasks for ac in task.covers}
    uncovered = tuple(ac for ac in criteria if ac not in covered)

    orphan_refs = tuple(
        sorted({ac for task in tasks for ac in task.covers if ac not in criteria_set}, key=_numeric_suffix)
    )

    unverified = tuple(
        task.task_id
        for task in tasks
        if not task.verified_by or not all(_selector_present(sel, test_corpus) for sel in task.verified_by)
    )

    return TraceReport(
        acceptance_criteria=criteria,
        tasks=tasks,
        uncovered_criteria=uncovered,
        orphan_task_refs=orphan_refs,
        unverified_tasks=unverified,
    )


def _selector_present(selector: str, corpus: str) -> bool:
    """Match a ``verified_by`` selector as a whole identifier, not a loose substring.

    Word-boundary matching avoids a false positive where a short selector is an accidental
    substring of an unrelated test name (e.g. ``test_replay`` matching ``test_replay_across_x``).
    """
    return re.search(rf"\b{re.escape(selector)}\b", corpus) is not None


def _numeric_suffix(identifier: str) -> int:
    digits = identifier.rsplit("-", 1)[-1]
    return int(digits) if digits.isdigit() else 0


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def run(spec: Path, tasks: Path, tests_dir: Path) -> TraceReport:
    return build_report(_read(spec), _read(tasks), collect_test_corpus(tests_dir))


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the spec<->task<->test traceability gate.")
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument("--tests-dir", type=Path, required=True)
    args = parser.parse_args(list(argv) if argv is not None else None)

    report = run(args.spec, args.tasks, args.tests_dir)
    print(report.render())
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
