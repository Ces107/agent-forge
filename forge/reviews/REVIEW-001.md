# REVIEW-001 — Adversarial Concurrency Review

Reviewer: AdversarialReviewer stage  
Date: 2026-06-01  
Codebase: `workspaces/ledger` (src/ledger/)  
Spec: `forge/work/spec.md`, ADR-003

---

## Attack surface covered

| # | Target | Result |
|---|--------|--------|
| 1 | Concurrent duplicate idempotency_key (retry storm) | PASS — exactly one posting pair produced |
| 2 | SQLITE_BUSY / OperationalError when busy_timeout expires | **FAIL — OperationalError leaks** |
| 3 | Lost update on shared account under interleaved concurrent transfers | PASS — serialisation via BEGIN IMMEDIATE works |
| 4 | Money conservation (global sum = 0 invariant) | PASS |
| 5 | Retry storm all-threads-domain-result (production busy_timeout) | PASS (passes with 5 s timeout; fails only when timeout is forced) |

---

## Finding F-001 — CRITICAL

**Severity:** CRITICAL  
**File:line:** `src/ledger/service.py:115`  
**Test:** `test_busy_timeout_does_not_leak_operational_error`

### Description

When `busy_timeout` is exhausted while waiting to acquire the SQLite write lock via
`BEGIN IMMEDIATE`, `sqlite3.OperationalError: database is locked` escapes `transfer()`
unhandled and surfaces to the API layer as an unmapped 500 Internal Server Error.

### Exact interleaving that triggers it

1. Connection A holds `BEGIN IMMEDIATE` (write lock in use).
2. Connection B calls `transfer(...)`.
3. `conn.execute("BEGIN IMMEDIATE")` at service.py:115 blocks, then times out after
   `busy_timeout` ms and raises `sqlite3.OperationalError`.
4. The outer `except Exception` at service.py:193 catches it, suppresses the `ROLLBACK`
   (since no transaction was opened), then **re-raises** the `OperationalError`.
5. `api.py:post_transfer` does not catch `sqlite3.OperationalError` — it only catches
   `AccountNotFound`, `InsufficientFunds`, `IdempotencyConflict`.
6. FastAPI returns HTTP 500.

Under the spec's "exactly-once money movement under concurrent RETRY STORMS" guarantee,
any thread that was part of a retry storm MUST either receive the idempotent result
(replayed=True) or a retryable error. An unmapped 500 breaks both the API contract
and the user-visible idempotency guarantee.

This is latent at `busy_timeout=5000ms` (the default) because the 5-second window is
long enough that contention resolves before timeout in normal testing. It becomes
observable when: (a) timeout is forced low, (b) a long-running test holds the lock,
or (c) under a genuine storm with many writers where the queue depth exceeds the timeout.

### Reproducing test output

```
FAILED tests/test_concurrency.py::test_busy_timeout_does_not_leak_operational_error

    ...
    >       conn.execute("BEGIN IMMEDIATE")
    E       sqlite3.OperationalError: database is locked
    src\ledger\service.py:115: OperationalError
    ...
    >               pytest.fail(
                        f"sqlite3.OperationalError leaked from transfer() instead of a domain error: {exc}"
                    )
    E               Failed: sqlite3.OperationalError leaked from transfer() instead of a domain error: database is locked
    tests\test_concurrency.py:265: Failed
```

Full pytest line:
```
FAILED tests/test_concurrency.py::test_busy_timeout_does_not_leak_operational_error - Failed: sqlite3.OperationalError leaked from transfer() instead of a domain error: database is locked
```

### Proposed fix (<=3 lines)

In `service.py`, add a `except sqlite3.OperationalError` handler around `conn.execute("BEGIN IMMEDIATE")` (or wrap the entire function body) that catches lock-timeout errors and raises a mapped domain error — for example a new `LedgerError` subclass `ServiceUnavailable` — which `api.py` then maps to HTTP 503 with `Retry-After: 1`.

```python
# service.py — inside transfer(), replace line 115 bare call:
try:
    conn.execute("BEGIN IMMEDIATE")
except sqlite3.OperationalError as exc:
    raise ServiceUnavailable("Write lock unavailable; retry") from exc
```

```python
# api.py — add to the except chain in post_transfer:
except ServiceUnavailable as exc:
    raise HTTPException(status_code=503, headers={"Retry-After": "1"}, detail=str(exc)) from exc
```

---

## Other findings (no reproduction failure)

### F-002 — LOW: `AccountNotFound` leaks a successfully-claimed idempotency key

**File:line:** `src/ledger/service.py:154-156`

When `from_account` does not exist, the idempotency key was already inserted inside
the `BEGIN IMMEDIATE` transaction. The `ROLLBACK` at line 155 correctly discards it,
but the idempotency record is gone. A retry of the same `(key, nonexistent-account)`
call will NOT return the idempotent error — it will re-attempt and raise `AccountNotFound`
again (same observable behaviour, so not a money-safety bug). The spec says replay
returns the original result; for error paths this is ambiguous but worth noting.
No failing test produced — the spec does not explicitly cover error-path idempotency.

### F-003 — LOW: `_result_from_json` ignores stored `replayed` field

**File:line:** `src/ledger/service.py:79-88`

`_result_from_json(raw, replayed=True)` always forces `replayed=True` from the call
site. The stored JSON contains `"replayed": false` (written at first-commit time).
The `replayed` field from the stored JSON is therefore ignored. This is correct
behaviour for replays (we WANT `True`) but it means the stored JSON is misleading and
the field is never read back — a maintenance hazard.

---

## Real pytest output (complete run)

```
============================= test session starts =============================
platform win32 -- Python 3.14.0, pytest-9.0.3, pluggy-1.6.0
rootdir: C:\Users\cpereiro\IdeaProjects\agent-forge\workspaces\ledger
configfile: pyproject.toml

tests/test_concurrency.py::test_concurrent_same_idempotency_key_creates_exactly_one_posting_pair PASSED [ 20%]
tests/test_concurrency.py::test_busy_timeout_does_not_leak_operational_error FAILED [ 40%]
tests/test_concurrency.py::test_no_lost_update_concurrent_drains PASSED  [ 60%]
tests/test_concurrency.py::test_money_conservation_under_concurrent_load PASSED [ 80%]
tests/test_concurrency.py::test_retry_storm_all_threads_return_domain_result PASSED [100%]

========================= 1 failed, 4 passed in 2.27s =========================
```

---

## Verdict

GATE: FAIL

Failing test: `test_busy_timeout_does_not_leak_operational_error`  
Root cause: `src/ledger/service.py:115` — `conn.execute("BEGIN IMMEDIATE")` raises `sqlite3.OperationalError` on lock timeout; this error is not caught by the domain exception handlers in `transfer()` or `api.py`, so it leaks as HTTP 500 under write-lock contention.  
One-sentence description: When the `busy_timeout` expires waiting for the SQLite write lock, `transfer()` raises a raw `sqlite3.OperationalError` instead of a domain error, breaking the "exactly-once under concurrent retry storms" contract and producing an unmapped HTTP 500.  
Proposed fix: catch `sqlite3.OperationalError` at the `BEGIN IMMEDIATE` call site in `service.py` and re-raise as a new `ServiceUnavailable(LedgerError)` subclass; add a 503 handler in `api.py`.
