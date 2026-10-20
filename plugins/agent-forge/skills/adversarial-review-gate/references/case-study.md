# Case study: the review gate catching a real concurrency bug

Source: the agent-forge build of an idempotent double-entry payments ledger
(https://github.com/Ces107/agent-forge). Three consecutive commits show the cycle.

## The code under review

A SQLite-backed ledger. Each transfer opens `BEGIN IMMEDIATE`, claims the idempotency key, checks
the balance floor, writes a debit and a credit, and commits. The implementer's tests (unit tests
plus Hypothesis stateful tests) were all green.

## The attack

The reviewer's hypothesis: *under a retry storm, lock contention can last longer than SQLite's
`busy_timeout`; then `BEGIN IMMEDIATE` raises `sqlite3.OperationalError: database is locked`, which
nothing catches.* That breaks "exactly-once under retries", because the caller gets an HTTP 500 and
cannot tell whether the transfer happened.

The reviewer wrote five attack tests. Four held (concurrent duplicate keys behind a
`threading.Barrier`, lost update on a shared account, money conservation under load, and a retry
storm at the production 5 s timeout). The fifth made the timeout deterministic instead of hoping a
storm would hit it:

1. Create a file-backed database (an in-memory one would serialise for you and hide the bug).
2. Connection A opens `BEGIN IMMEDIATE` and holds the write lock.
3. Connection B sets `PRAGMA busy_timeout=1` (1 ms, a guaranteed timeout) and calls `transfer()`.
4. Assert that B gets a typed domain error, never a raw `sqlite3.OperationalError`.

`test_busy_timeout_does_not_leak_operational_error` failed against the implementation:
`GATE: FAIL`, committed as a red test on its own. A storm test alone would have passed and the bug
would have shipped. Forcing the bad interleaving is what found it.

## The fix (by the implementer, not the reviewer)

Bounded retry with backoff around `BEGIN IMMEDIATE`, and a typed `ServiceUnavailable` error mapped
to HTTP 503 with `Retry-After` when the retry budget is exhausted. A lock timeout became a response the
client is told to retry, not a 500.

## The re-run

The verifier re-ran the full suite, coverage, lint, types and security scan: `VERIFY: PASS`.

## What made it work

- The reviewer had to produce a failing test, so "looks fine" was not an option.
- The test used real threads and a real file database, so the race could actually happen.
- The red test was committed before the fix, so the history proves the review added value.
