# Tech-debt & defect backlog — agent-forge

Findings caught during the pipeline run. Zero findings on >300 LOC of generated code = the review
was not done. Format: `TD-NNN <SEVERITY> <one-line>` + file:line + fix + status.

## TD-001 HIGH — SQLITE_BUSY leaks as unhandled OperationalError under retry storms
- **Caught by:** AdversarialReview stage (REVIEW-001.md), reproducing test
  `tests/test_concurrency.py::test_busy_timeout_does_not_leak_operational_error`.
- **file:line:** `src/ledger/service.py:115` (`conn.execute("BEGIN IMMEDIATE")`).
- **Defect:** when the write lock cannot be acquired within `busy_timeout`, `BEGIN IMMEDIATE`
  raises `sqlite3.OperationalError: database is locked`. It is not caught (the inner handler only
  covers `IntegrityError`); it leaks past `api.py` (which maps only domain errors) as HTTP 500.
  Directly violates the spec guarantee "exactly-once money movement under concurrent retry storms":
  a duplicate retry that loses the lock race errors out instead of returning the idempotent result.
- **Fix:** bounded retry-with-backoff around `BEGIN IMMEDIATE` on `OperationalError("database is
  locked")`; map residual contention to a typed `ServiceUnavailable` → HTTP 503; add regression test.
- **Status:** RESOLVED — `_begin_immediate()` retries (50/100 ms backoff, 3 attempts) then raises
  `ServiceUnavailable`; api.py maps it to 503 + `Retry-After`. Reviewer test now passes.

## TD-002 LOW — amount<=0 relies solely on the API/Pydantic layer
- **file:line:** `src/ledger/service.py` (no service-level guard).
- **Defect:** the service trusts the caller for `amount > 0`; a direct library call with amount<=0
  would surface a raw `sqlite3.IntegrityError` (schema CHECK) rather than a domain error.
- **Fix:** validate `amount > 0` in `transfer()` and raise a domain error.
- **Status:** RESOLVED — `transfer()` now raises `InvalidAmount` up front (api.py maps to 422).

## TD-003 LOW — FastAPI on_event("startup") deprecated (33 warnings on run)
- **Caught by:** Verify stage (deprecation warnings in the pytest run).
- **file:line:** `src/ledger/api.py` startup handler.
- **Fix:** migrate to a `lifespan` async context manager.
- **Status:** RESOLVED — warnings dropped from 33 to 1 (the last is third-party).

## Closing sweep (Verify gate, post-fix)
- Coverage 97.9% (≥80 gate); ruff + mypy --strict + bandit all exit 0; no secrets.
- The Verify gate HALTED the pipeline twice before passing: first on coverage 68% < 80 (api.py
  untested) and 11 ruff findings, then green after the hardening pass. The gate has teeth — it
  blocked progress on real, measured failures rather than rubber-stamping. See forge/reviews/VERIFY-001.md.

---

# Sweep — meta-layer SOTA upgrade (2026-06-01, v0.2.0)

Session added the spec-driven Tasks stage + EARS criteria, `forge/observability.py` (OTel GenAI
spans), `forge/traceability.py` (spec<->task<->test gate), planner agent, and CI/Makefile wiring.
~1,300 net new LOC in the meta-layer. Self-adversarial review of that new code:

## TD-004 HIGH — traceability gate (as first written) could only pass on a re-run, never greenfield
- **Caught by:** self-review during this session, before commit.
- **Defect:** the gate ran right after the Tasks stage and asserted each task's `verified_by` test
  already existed. On a real first build the tests are written *later* (Implement stage), so the
  gate would FAIL on every genuine greenfield run and only pass on a re-run with tests present.
- **Fix:** split into two phases. `gate_task_coverage()` (after Tasks) checks only spec<->task
  coverage via `TraceReport.structure_ok` (no test dependency); `gate_traceability()` (after Verify,
  once tests exist) checks the full bijection via `TraceReport.ok`.
- **Status:** RESOLVED — `forge/orchestrator.py` wires both gates; tests cover both phases.

## TD-005 MEDIUM — `verified_by` matched as a loose substring (false-positive coverage)
- **file:line:** `forge/traceability.py` `_selector_present`.
- **Defect:** a selector like `test_replay` would match `test_replay_across_restart`, letting a task
  claim a test that is not the one named — coverage theatre.
- **Fix:** word-boundary regex match (`\b<selector>\b`) so a selector binds to a whole identifier.
- **Status:** RESOLVED — `test_selector_matches_whole_identifier_not_substring` guards it.

## TD-006 MEDIUM — OTel spans always report zero token usage
- **file:line:** `forge/orchestrator.py` `run_stage` (passes `TokenUsage()`).
- **Defect:** `gen_ai.usage.input_tokens/output_tokens` are structurally correct but always 0; the
  headless `claude -p` output is not parsed for real counts, so cost/usage analytics are blind.
- **Fix:** parse the CLI's usage/JSON output (or `--output-format json`) into `TokenUsage`.
- **Status:** OPEN — deferred; low risk (observability shape is right, values are placeholder).

## TD-007 LOW — the live-CLI path (`run_stage`, `log_spawn`) is not integration-tested
- **Defect:** unit tests cover plan/gates/format; the actual subprocess spawn + span emission path
  is `# pragma: no cover`. A regression in argv construction or span wiring would not be caught.
- **Fix:** an integration test with a fake `claude` shim on PATH asserting a span lands in the
  trajectory NDJSON.
- **Status:** OPEN — deferred.

## TD-008 MEDIUM — AC-5 (crash-safety) traces to atomicity invariants, not an injected fault
- **Defect:** the spec promises a "simulated failure between postings" test. AC-5 currently traces
  to the Hypothesis invariants `transfer_ids_have_exactly_two_postings` + `books_close_globally`,
  which prove no transfer is left half-posted by *design* (single transaction) but do not inject a
  mid-transaction crash. Honest gap: atomicity is argued, not fault-injected.
- **Fix:** add a test that kills/aborts between the debit and credit and asserts books still close;
  re-point AC-5 `verified_by` at it.
- **Status:** OPEN — flagged so coverage is not overclaimed.

## TD-009 LOW — STATE-OF-THE-ART.md star counts / SWE-bench numbers are approximate
- **Defect:** figures are from secondary 2026 sources (marked `≈`), not pulled from each repo.
- **Fix:** periodic refresh against the repos directly.
- **Status:** OPEN — low priority, already disclosed as approximate.

## Closing sweep (meta-layer gates)
- `forge/tests`: 40 tests pass; coverage 98.2% (≥90 gate). ruff + mypy --strict + bandit exit 0.
- Real traceability gate against the ledger: `TRACE: PASS` (9 AC, 9 tasks, all tests real).
- Ledger suite unaffected: 98% coverage, green. Provenance audit: PASS (no app-code touched).

