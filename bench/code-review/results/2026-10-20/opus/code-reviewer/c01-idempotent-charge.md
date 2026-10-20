## Review Summary

Serious issues: this does not yet prevent the double charge it was written for. The read-then-capture check is not atomic and the index isn't unique, so two retries arriving together still both hit the gateway. On top of that, the schema change never reaches an existing database, so `connect()` fails on the first deploy. Both tests pass, but they only cover the sequential, fresh-database case.

### Critical (must fix before merge)

**payments/service.py:16-22, payments/db.py:14**: Check-then-act race. Concurrent retries with the same key still double-charge. The `SELECT ... WHERE idempotency_key = ?` and the later `INSERT` are separate steps with `gateway.capture()` in between, and `charges_idem` is a plain index, so nothing in the database rejects a second row with the same key. The PR targets a client that retries on timeout, which means the retry typically arrives *while the first request is still capturing*. That is exactly the window this code leaves open. I reproduced it with two threads and a gateway that takes 200 ms: `captured == [('cus_1', 500), ('cus_1', 500)]`, and the two calls returned different charge ids.

The fix is to reserve the key under a UNIQUE constraint *before* calling the gateway, and to treat a constraint violation as "this is a retry":

```python
def charge(conn, gateway, customer_id, amount_cents, idempotency_key):
    if amount_cents <= 0:
        raise ValueError("amount must be positive")
    charge_id = uuid.uuid4().hex
    try:
        with conn:  # reserve the key first; the UNIQUE index makes this the single winner
            conn.execute(
                "INSERT INTO charges (charge_id, customer_id, amount_cents, txn_id, idempotency_key)"
                " VALUES (?, ?, ?, '', ?)",
                (charge_id, customer_id, amount_cents, idempotency_key),
            )
    except sqlite3.IntegrityError:
        row = conn.execute(
            "SELECT charge_id, amount_cents FROM charges"
            " WHERE customer_id = ? AND idempotency_key = ?",
            (customer_id, idempotency_key),
        ).fetchone()
        if row[1] != amount_cents:
            raise IdempotencyConflict(idempotency_key)  # map to 409/422 in the handler
        return row[0]
    txn_id = gateway.capture(customer_id, amount_cents)
    with conn:
        conn.execute("UPDATE charges SET txn_id = ? WHERE charge_id = ?", (txn_id, charge_id))
    return charge_id
```

I checked this version: with five concurrent calls on one key, there was 1 capture and 1 distinct id. A retry that lands while the first call is still in flight now gets back a charge whose `txn_id` is still `''`. Decide whether the API returns that as "pending" or answers 409 until the capture finishes.

**payments/db.py:6-14**: The migration never runs against an existing database. `CREATE TABLE IF NOT EXISTS` is a no-op when `charges` already exists, so production keeps the old four-column table. The next statement, `CREATE INDEX ... ON charges (idempotency_key)`, then raises `sqlite3.OperationalError: no such column: idempotency_key`, which makes `connect()` fail and every charge request with it. I reproduced this against a table created with the `main` schema. Add the column explicitly:

```python
cols = {row[1] for row in conn.execute("PRAGMA table_info(charges)")}
if "idempotency_key" not in cols:
    conn.execute("ALTER TABLE charges ADD COLUMN idempotency_key TEXT")
conn.execute(
    "CREATE UNIQUE INDEX IF NOT EXISTS charges_idem"
    " ON charges (customer_id, idempotency_key)"
)
conn.commit()
```

Existing rows keep `NULL` keys, and SQLite allows multiple NULLs under a UNIQUE index, so legacy data does not block the index.

### Suggestions (should fix)

**payments/service.py:16-20**: The key is matched globally, and the request is never compared with the original. If `cus_2` sends a key that `cus_1` already used, the call returns `cus_1`'s `charge_id` and charges `cus_2` nothing. I confirmed this: two calls with key `"k"`, one for `cus_1`/500 and one for `cus_2`/99999, returned the same id with a single capture. That is a silent lost charge and leaks another customer's charge id. Scope the key to the customer, which the `(customer_id, idempotency_key)` index above does, and reject a reused key whose amount differs instead of returning the old charge as if it matched.

**payments/service.py:22-28**: Capture first, persist second still loses money if the process dies or the commit fails after `gateway.capture()` returns. `connect()` uses `timeout=5`, so "database is locked" is a realistic failure here. The retry then finds no row and captures again. Reserving the row first, as above, narrows the gap. The robust fix is to forward `idempotency_key` to the processor, if its API supports one, so the gateway itself deduplicates. If `capture()` raises, also delete or mark the reserved row so a legitimate retry is not stuck.

**tests/test_service.py:13-19**: The new test only covers two sequential calls on a fresh database, the one case that already worked. Add a concurrent test (two threads plus a slow fake gateway, assert `len(gateway.captured) == 1`) and a test that runs `connect()` on a database created with the old schema. Each of those would have caught one of the critical issues above.

### What looks good

Making `idempotency_key` a required parameter of `charge()` means no call site can silently skip deduplication. The existing test was updated to pass a key rather than left calling the old signature.
