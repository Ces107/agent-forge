# Attack surfaces, by category

Pick the categories that match what the change does. For each item, ask: what is the smallest test
that fails if this bug is present?

## 1. Concurrency and races
- **Check-then-act (TOCTOU):** reads a row, decides, then writes in a separate step. Two callers
  both pass the check.
- **Lost update:** read-modify-write of a counter, balance or status without a lock, an atomic
  update or optimistic version check.
- **Lock timeout leak:** contention beyond the database's busy or lock timeout surfaces as a raw
  driver error (HTTP 500) instead of a typed, retryable result (HTTP 503 + `Retry-After`).
- **Unique-constraint race:** the code checks for existence before inserting instead of letting
  the constraint be the arbiter.
- **Shared mutable state** in module globals, singletons or caches across requests or threads.

## 2. Idempotency and retries
- The same idempotency key sent **concurrently** produces two side effects.
- A **replay after restart** does not find the stored result (it lived in memory).
- The same key with a **different payload** is silently accepted instead of rejected.
- Retries wrap a **non-idempotent** operation (email, charge, webhook) without a dedupe key.
- **Error responses:** decide whether they are cached for replay, and check the code does what the
  design says.

## 3. Atomicity and partial failure
- A crash between two writes that must commit together (debit/credit, order/stock, row/outbox).
- An **external call inside a database transaction** (holds locks for network latency; if the
  transaction rolls back, the external side effect has still happened).
- Exception handlers that **swallow** errors and return success.
- Cleanup that runs only on the happy path (missing `finally`, `defer`, or context manager).

## 4. Boundaries and input
- Zero, negative, one, maximum integer, overflow; empty string, whitespace, very long input.
- Unicode: combining characters, right-to-left text, case folding, normalisation (NFC/NFD).
- Missing versus null versus empty fields; duplicate keys in JSON; unexpected extra fields.
- Pagination: last page, empty page, page size 0 or huge.

## 5. Money and numbers
- Floating point for currency; rounding direction; splitting an amount that does not divide.
- Mixed currencies summed together; amount without currency.
- A path that creates or destroys value (sum of all postings ≠ 0).

## 6. Time
- Time zones and DST transitions; "end of day" in which zone.
- Clock skew between machines; using wall-clock time for ordering or expiry.
- Leap years, month ends, 31 → 30-day rollovers.

## 7. Security
- **Authorization:** can user A read or change user B's object by changing an id (IDOR)?
- **Injection:** SQL, shell, template, path traversal (`../`), header injection.
- **Secrets** in logs, error messages, URLs or committed config.
- Unbounded input that allows resource exhaustion (huge upload, regex backtracking).

## 8. Error mapping and observability
- Domain errors mapped to the wrong status (validation → 500, conflict → 400).
- Errors that lose the cause (re-raised without context), or leak internals to the client.
- Retries without backoff or limit.

## 9. Resources
- Connections, files or locks not released on error paths.
- Unbounded queues, caches or in-memory lists that grow with traffic.
