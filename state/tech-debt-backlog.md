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

