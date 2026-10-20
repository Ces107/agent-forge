## Review Summary

The idempotency key is stored and the retry path returns the original charge, and the tests pass. It is not safe to merge yet: the check-then-capture sequence is not atomic, so two concurrent retries can still double-charge, which is the exact incident this PR is meant to prevent. A retry with the same key but a different amount is also silently accepted. Needs changes before merge.

### Critical (must fix before merge)

**payments/service.py:16-26 (`charge`)** — The idempotency lookup (`SELECT ... WHERE idempotency_key = ?`) runs before `gateway.capture()`, with no lock and no uniqueness constraint. Two requests with the same key that arrive together both see no row, both call `capture()`, and both insert. This is the double-charge the PR describes, still possible under concurrency.

**payments/db.py (index creation)** — `idempotency_key` is nullable and the index is `CREATE INDEX` (not `UNIQUE`). Nothing in the schema prevents a second row with the same key, so the application check is the only guard.

**payments/service.py:16-17** — A retry with the same key but a different `amount_cents` or `customer_id` returns the first charge's id without any error. The caller believes the new amount was charged when it was not. Compare the stored amount and customer, and raise on mismatch.

Suggested direction: make the key unique at the database level and reserve the row before calling the gateway.

```python
# payments/db.py
conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS charges_idem ON charges (idempotency_key)")
```

```python
# payments/service.py (sketch)
existing = conn.execute(
    "SELECT charge_id, customer_id, amount_cents FROM charges WHERE idempotency_key = ?",
    (idempotency_key,),
).fetchone()
if existing:
    if (existing[1], existing[2]) != (customer_id, amount_cents):
        raise ValueError("idempotency key reused with different parameters")
    return existing[0]
# Insert a pending row under the UNIQUE constraint, catch IntegrityError on a
# concurrent insert and return the winner's charge_id, then capture and update.
```

### Suggestions (should fix)

**payments/service.py:22-27** — `gateway.capture()` runs before the insert is committed. If the process dies between capture and commit, the key is not recorded and the next retry charges again. Recording the key before capture (status `pending`) closes that window.

**tests/test_service.py** — The new test only covers the sequential retry. Add a test for the same key with a different amount, and one for a reused key after a failed capture.

### Nitpicks (optional)

None worth raising beyond the points above.

### What looks good

Returning the stored `charge_id` on a repeated key, instead of raising, is the behaviour clients need when they retry on timeouts. The amount-positive guard stays in place before any lookup.
