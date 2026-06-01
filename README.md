# agent-forge

**An autonomous SDLC factory, and the payments ledger it built — every line of application code authored by AI agents, every claim auditable from `git log`.**

[![ci](https://github.com/Ces107/agent-forge/actions/workflows/ci.yml/badge.svg)](https://github.com/Ces107/agent-forge/actions/workflows/ci.yml)

The top level of this repo **is** a reusable multi-agent build pipeline. Inside
`workspaces/ledger/` is a real, runnable artifact it produced: an idempotent double-entry
payments ledger whose books always close. The interesting thing is not "an AI wrote some code" —
it is that the process is **legible and falsifiable**: you can trace every design decision, every
caught bug, and every fix to a specific agent and a specific commit.

## The 5-minute reviewer path

```bash
# 1. Who actually wrote the code? (provenance is machine-enforced, not a claim)
git log --format='%an  %s' -- workspaces/ledger/src
python hooks/audit_provenance.py        # exits 0 only if every app-code commit is agent-authored

# 2. Does it actually work? (no GPU, no external services)
cd workspaces/ledger && pip install -e ".[dev]" && python -m pytest
#   ...or: docker compose up --build

# 3. What did the autonomous run actually do?
open AUTONOMOUS-TRAJECTORY.md            # opens with the bug the adversarial agent caught
```

## The pipeline (`forge/`)

```
Spec ──▶ Architect ──▶ Tasks ──▶ Implement ──▶ AdversarialReview ──▶ Verify ──▶ Commit
(opus)    (opus)        (sonnet)  (sonnet)       (sonnet)             (haiku)
```

This is **spec-driven development** (after GitHub Spec Kit, AWS Kiro and BMAD-METHOD), with the
spec→code link made *falsifiable* rather than advisory. Each stage is a Claude Code sub-agent in
`.claude/agents/` with a restricted toolset and an explicit model tier. The orchestrator
(`forge/orchestrator.py`) runs them in order and enforces three gates **with teeth**:

- **Task-coverage gate** (after Tasks) — halts unless every EARS acceptance criterion (`AC-N`) in
  the spec is covered by a task and no task references a phantom criterion.
- **AdversarialReview gate** — halts unless the reviewer produces a structured finding
  (`forge/reviews/REVIEW-NNN.md`). A review that says "looks fine" is rejected.
- **Verify gate** — halts unless the test suite, coverage (≥80%), `ruff`, `mypy --strict` and
  `bandit` all exit 0. Completion claims without runtime evidence are worth zero.
- **Traceability gate** (after Verify) — the full bijection: every `AC-N` traces to a task and every
  task names a test that now actually exists (`forge/traceability.py`). No dropped requirement, no
  task pointing at a phantom test. Run it standalone: `make trace`.

Every stage spawn is recorded as an **OpenTelemetry GenAI span** (`forge/observability.py`,
`gen_ai.*` semantic conventions) carrying a `forge.provenance` attribute, so observability and
authorship are one auditable record. The meta-layer has its own gates (`make forge-test forge-lint
forge-types`, ≥90% coverage, `mypy --strict`).

Re-run the build yourself (requires the `claude` CLI): `make pipeline`.

**Where this sits in the field, and where it goes beyond it:** see
[`docs/STATE-OF-THE-ART.md`](docs/STATE-OF-THE-ART.md) — the projects this draws from (OpenHands,
SWE-agent, Spec Kit, Kiro, BMAD, LangGraph, Letta/Mem0, Langfuse/OTel) and the auditability delta
agent-forge adds on top.

## The inner project (`workspaces/ledger/`)

A payments ledger scoped to the single hardest correctness problem: **exactly-once money movement
under concurrent retry storms**. Double-entry postings, idempotency keys, no overdraft, books that
close globally. Correctness is verified by a Hypothesis stateful model plus concurrency and
crash-recovery tests — not happy-path unit tests. See `forge/work/spec.md`.

## Provenance

Application-code and design commits are authored by per-role agent identities
(`* <implementer@agent-forge.bot>`, etc.) or carry a `Co-Authored-By: Claude` trailer; the human
appears only on scaffold and merge commits. `hooks/audit_provenance.py` enforces this in CI, so the
"agent-built" claim is falsifiable rather than asserted.
