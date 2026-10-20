# Code review: Import supplier price lists from CSV

**Verdict: BLOCK** · 1 finding (1 proven) · Scope: main..feature, 3 files, +74 −0 ·
Tests: `python -m pytest` passed (6)

`import_price_list()` parses the CSV lazily and `replace_price_list()` deletes and re-inserts the
list inside one `with conn:` transaction. The atomicity claim holds: a bad row or a parse error
rolls back and the old prices stay. But every failed import leaks a pooled connection, because
`pool.release(conn)` is only reached on success; after four rejected uploads (pool size 4) every
price read and write in the process fails with `PoolExhausted` until a restart. Release the
connection in a `finally` before merging.

## Findings

### 1. [CRITICAL · Proven] `catalog/prices.py:34-43`: a failed import never returns its connection, so a few bad files take the catalog down
**What happens:** merchandising uploads a file with a negative price, a duplicate SKU, a
decimal comma (`"12,50"`), a missing column or an Excel BOM → the import raises (as intended)
and the transaction rolls back, but the exception skips `pool.release(conn)` at line 43. The
semaphore slot is never released. After `size` such uploads (4 by default) `pool.acquire()` times
out for every caller, so `get_price`, `set_price` and further imports all raise `PoolExhausted`:
an outage of every catalog path, lasting until the process restarts. The PR's own failure test
(`test_bad_row_keeps_previous_prices`) leaks a connection too; it passes only because it does
not use the pool afterwards. The sibling functions `get_price`/`set_price` use
`try/finally: pool.release(conn)`; the new function dropped the guard.

**Evidence:**
```
$ python -m pytest tests/test_review_proof_pool_leak.py
E           catalog.pool.PoolExhausted: no database connection available
E           catalog.pool.PoolExhausted: no database connection available
2 failed in 2.28s
```
(first test: four rejected uploads, then a plain `get_price`; second: one upload with a decimal
comma on a pool of size 1.)

**Fix:**
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

<details><summary>Proof test (add it to the suite)</summary>

```python
import sqlite3

import pytest

from catalog.importer import import_price_list
from catalog.pool import ConnectionPool
from catalog.prices import get_price, set_price
from catalog.schema import init_schema


def test_review_proof_bad_imports_do_not_exhaust_the_pool(tmp_path):
    pool = ConnectionPool(str(tmp_path / "catalog.db"), size=4, timeout=0.2)
    init_schema(pool)
    set_price(pool, "eu", "A-1", 1000)
    for _ in range(4):
        with pytest.raises(sqlite3.IntegrityError):
            import_price_list(pool, "eu", "sku,price\nA-1,12.50\nB-2,-1\n")
    assert get_price(pool, "eu", "A-1") == 1000


def test_review_proof_parse_error_also_leaks(tmp_path):
    pool = ConnectionPool(str(tmp_path / "catalog.db"), size=1, timeout=0.2)
    init_schema(pool)
    with pytest.raises(Exception):
        import_price_list(pool, "eu", 'sku,price\nA-1,"12,50"\n')
    assert get_price(pool, "eu", "A-1") is None
```
</details>

## Questions for the author
- An empty upload (or a header-only file) deletes the whole list and returns 0
  (`import_price_list(pool, "eu", "")` → `0`, then `get_price(pool, "eu", "A-1")` → `None`).
  Is "replace with nothing" a supported operation, or should zero rows be rejected like any
  other bad file?
- Files saved by Excel as "CSV UTF-8" start with a BOM, and `parse_rows` then fails with
  `KeyError('sku')`. Does the upload handler decode with `utf-8-sig`? It is not in this repo.

## Checked and fine
- Atomicity: the DELETE and every INSERT run on one connection inside `with conn:`; the
  generator is consumed inside the block, so parse errors also roll back (the old price survived
  in both proofs before the pool ran out).
- Other lists are untouched: the DELETE is filtered by `list_id` and parameterised.
- Money: prices are parsed with `Decimal` and rounded `ROUND_HALF_UP` to integer cents, no float.
- Negative prices are rejected by the schema's `CHECK (cents >= 0)`; duplicate SKUs in one file
  hit the primary key and roll back.
- `Decimal` accepts surrounding whitespace, so `" 12.50 "` parses; `NaN`/`Infinity` raise on
  `int()` and roll back.
