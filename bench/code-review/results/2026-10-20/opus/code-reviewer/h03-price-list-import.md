## Review Summary

The rollback works: a bad row leaves the old prices in place, as the PR says. There is one serious bug on that same failure path, though. Every failed import permanently uses up a pool connection, so a few bad uploads take down every database call in the process. There is also one data-loss gap the "bad file keeps previous prices" promise doesn't cover.

### Critical (must fix before merge)

**catalog/prices.py:34-43** — `pool.release(conn)` only runs on the success path. If anything raises inside `with conn:` (a CHECK violation, a duplicate SKU, a price that isn't a number, a missing column, all raised lazily from the `parse_rows` generator), the transaction rolls back but the connection never goes back to the pool and its semaphore slot is never released. I reproduced this on a `size=4` pool. After four bad uploads (`-1`, `abc`, header `SKU,Price`, a duplicated SKU), the next ordinary `get_price()` raises `PoolExhausted: no database connection available`. Every request thread is then down until the process restarts. The other functions in this module (`get_price`, `set_price`, `init_schema`) already use `try/finally`; this one needs the same:

```python
def replace_price_list(pool: ConnectionPool, list_id: str, rows: Iterable[tuple[str, int]]) -> int:
    """Atomically replace every price in ``list_id``; returns how many prices were written."""
    conn = pool.acquire()
    try:
        count = 0
        with conn:
            conn.execute("DELETE FROM prices WHERE list_id = ?", (list_id,))
            for sku, cents in rows:
                conn.execute(
                    "INSERT INTO prices (list_id, sku, cents) VALUES (?, ?, ?)", (list_id, sku, cents)
                )
                count += 1
        return count
    finally:
        pool.release(conn)
```

`test_bad_row_keeps_previous_prices` would catch this if it also asserted the pool still works afterwards. For example, run the bad import five times with a `size=4` pool, then call `get_price`.

### Suggestions (should fix)

**catalog/importer.py:19-20 / catalog/prices.py:37** — An empty upload, or one with only a header row, deletes the whole list and inserts nothing: `import_price_list(pool, "eu", "")` returns `0`, and every price in `eu` is gone. A blank or truncated file is exactly the "bad file" the PR says leaves the previous prices in place, and an empty price list means products with no price. Reject an import that produces no rows (or require an explicit `allow_empty=True`):

```python
def import_price_list(pool: ConnectionPool, list_id: str, text: str) -> int:
    rows = list(parse_rows(text))
    if not rows:
        raise ValueError(f"price list {list_id!r} upload contains no rows; refusing to clear it")
    return replace_price_list(pool, list_id, rows)
```

**catalog/importer.py:12-16** — A bad file reaches the person who uploaded it as a raw `KeyError: 'price'`, `decimal.InvalidOperation [<class 'decimal.ConversionSyntax'>]` or `UNIQUE constraint failed`, with no line number. Merchandising can't fix their CSV from that. Parsing the whole file before opening the transaction (as above) also lets you check each row and raise one domain error naming the line, such as `line 3: price 'abc' is not a number` or `line 7: duplicate sku A-1`. It also shortens the window in which the SQLite write lock is held. Right now the lock is taken by the `DELETE` and kept while the generator parses the rest of the file, which can push concurrent `set_price` calls into `database is locked` on a large upload.

### What looks good

Doing the `DELETE` and every `INSERT` inside one `with conn:` transaction is the right way to make the replace atomic, and the rollback works as promised. The `ROUND_HALF_UP` quantize on `Decimal` avoids float rounding when converting euros to cents.
