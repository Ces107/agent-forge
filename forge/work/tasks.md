# Tasks — idempotent double-entry ledger

Spec-driven decomposition (Spec Kit / Kiro / BMAD shape, made falsifiable). Each task `T-N`
declares the acceptance criteria it `covers:` and the real test that verifies it (`verified_by:`).
`forge.traceability` checks, in CI, that every `AC-N` in `forge/work/spec.md` is covered by a task
and that every `verified_by:` selector exists in `workspaces/ledger/tests/`. No orphan requirement,
no task pointing at a test that was never written.

Run the gate:

```bash
python -m forge.traceability \
    --spec forge/work/spec.md --tasks forge/work/tasks.md --tests-dir workspaces/ledger/tests
```

---

## T-1 — Double-entry posting primitive
Write each accepted transfer as one immutable debit + one matching credit in a single transaction.
covers: AC-1
verified_by: test_transfer_writes_one_debit_and_one_credit

## T-2 — Idempotency store with replayed-response
Persist `(idempotency_key -> stored result)`; a replay returns the original and posts nothing.
covers: AC-2
verified_by: test_replay_returns_same_result, test_post_transfer_replay_no_new_postings

## T-3 — Race-safe idempotency under concurrent duplicates
Serialise the check-then-insert so N concurrent duplicates yield exactly one posting pair.
covers: AC-3
verified_by: test_concurrent_same_idempotency_key_creates_exactly_one_posting_pair

## T-4 — Serialised balance mutation (no lost update)
`BEGIN IMMEDIATE` write-lock so interleaved transfers on one account cannot overwrite each other.
covers: AC-4
verified_by: test_no_lost_update_concurrent_drains

## T-5 — Atomic two-posting transaction (crash-safety)
Both postings commit atomically or neither does. A Hypothesis stateful model asserts the invariant
after every step that each transfer id has exactly two postings and the books close, AND a dedicated
crash-recovery test kills the process between the debit and the credit (a BaseException escaping the
rollback handler) and proves a fresh connection still sees closed books with no half-transfer.
covers: AC-5
verified_by: transfer_ids_have_exactly_two_postings, books_close_globally, test_crash_between_postings_leaves_books_closed

## T-6 — Money conservation invariant
No path creates or destroys value; conservation holds under concurrent load.
covers: AC-6
verified_by: test_money_conservation_under_concurrent_load

## T-7 — Overdraft floor enforcement
Reject any transfer that would breach the per-account floor; default floor 0, configurable.
covers: AC-7
verified_by: test_overdraft_raises_insufficient_funds, test_custom_floor_rejects_breach

## T-8 — Reconciliation proof endpoint
Expose global `sum(debits) == sum(credits)` and per-account balance == fold of postings.
covers: AC-8
verified_by: test_get_reconciliation_balanced, test_reconciliation_per_account_balance_matches_fold

## T-9 — Retry-storm resilience (the caught bug, now guarded)
Bounded retry/backoff on `SQLITE_BUSY`; a typed `ServiceUnavailable` -> HTTP 503 instead of a
leaked `OperationalError`. Regression test committed by the AdversarialReviewer (`REVIEW-001`).
covers: AC-9
verified_by: test_busy_timeout_does_not_leak_operational_error, test_retry_storm_all_threads_return_domain_result
