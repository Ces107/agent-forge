## Review Summary

The transactional replace is the right approach, and the rollback on a bad row works. But `replace_price_list` never returns its connection to the pool when the transaction fails. Each failed import leaks one of the four connections. After four bad uploads, every price read and write fails with `PoolExhausted`. This must be fixed before merge.

### Critical (must fix before merge)

**catalog/prices.py:37-41** — `pool.release(conn)` sits after the `with conn:` block with no `try/finally`. When an insert raises (for example `sqlite3.IntegrityError` from the `cents >= 0` check, or a `ValueError` from `parse_rows` on a bad price), the exception skips `release`. The semaphore slot is never freed. The rollback happens, but the connection is lost.

I reproduced it on `feature` with a pool of size 4 and a short timeout. Four failed imports followed by one `get_price` call:

```
fail 0..3  CHECK constraint failed: cents >= 0
get_price  -> PoolExhausted: no database connection available
```

One malformed upload from merchandising therefore takes down all price lookups and writes for the whole catalog until the process restarts. The existing `get_price` and `set_price` already use `try/finally`, so this one should match them.

```python
# Before
def replace_price_list(pool, list_id, rows):
    conn = pool.acquire()
    count = 0
    with conn:
        conn.execute("DELETE FROM prices WHERE list_id = ?", (list_id,))
        for sku, cents in rows:
            conn.execute(
                "INSERT INTO prices (list_id, sku, cents) VALUES (?, ?, ?)", (list_id, sku, cents)
            )
            count += 1
    pool.release(conn)
    return count

# After
def replace_price_list(pool, list_id, rows):
    conn = pool.acquire()
    count = 0
    try:
        with conn:
            conn.execute("DELETE FROM prices WHERE list_id = ?", (list_id,))
            for sku, cents in rows:
                conn.execute(
                    "INSERT INTO prices (list_id, sku, cents) VALUES (?, ?, ?)", (list_id, sku, cents)
                )
                count += 1
    finally:
        pool.release(conn)
    return count
```

Add a test that runs a failing import more times than the pool size (for example five times on a pool of size 4) and then checks that `get_price` still works. The current `test_bad_row_keeps_previous_prices` runs only once, so it does not show the leak.

### Suggestions (should fix)

**catalog/importer.py:9-12** — `parse_rows` is a generator consumed inside the transaction, so a bad row is found only after the `DELETE` has already run. The rollback covers it, but validating the file into a list before opening the transaction would make the failure cheaper and easier to reason about. Optional if the leak above is fixed.

### Nitpicks (optional)

None worth raising beyond the points above.

### What looks good

Putting the `DELETE` and the inserts in one `with conn:` block gives a real all-or-nothing replace, and the `test_bad_row_keeps_previous_prices` test checks that the old prices survive a failed import.
