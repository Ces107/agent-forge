# Code review: Import supplier price lists from CSV

**Verdict: BLOCK** · 1 finding (1 proven) · Scope: main..feature, 3 files, 74 insertions · Tests: `python -m pytest` passed on the existing 3 tests; the new proof test failed (shown below)

The change adds `import_price_list()`, which parses a CSV and calls `replace_price_list()` to swap a
whole price list inside one transaction. The rollback works: a bad row leaves the old prices in place,
as the PR says and the existing test checks. But `replace_price_list()` never releases its connection
when the transaction fails. Every failed upload permanently consumes one slot of the shared
`ConnectionPool`. After `size` failed imports (default 4) every database call in the app raises
`PoolExhausted` until the process restarts.

## Findings

### 1. [CRITICAL · Proven] `catalog/prices.py:34-44`: failed import leaks the pooled connection

**What happens:** Merchandising uploads a CSV with one bad row (a negative price, a duplicate SKU, a
missing column) → the `with conn:` block rolls back and the exception propagates, but
`pool.release(conn)` sits after the block and is skipped, so the semaphore slot is never returned.
Repeat `size` times and every `acquire()` (catalog reads, price writes, the whole app) waits `timeout`
seconds and raises `PoolExhausted` → the main path is down.

**Evidence:**
```
$ python -m pytest tests/test_review_proof_pool.py
>           raise PoolExhausted("no database connection available")
E           catalog.pool.PoolExhausted: no database connection available
catalog\pool.py:21: PoolExhausted
FAILED tests/test_review_proof_pool.py::test_review_proof_failed_imports_exhaust_pool
```
The test runs two failing imports on a pool of size 2 (each raises `IntegrityError` as expected); the
`get_price` call after them is the one that exhausts the pool. `catalog/pool.py` shows the slot is only
released by `release()`, and `acquire()` blocks on `self._slots.acquire(timeout=...)`.

**Fix:** release the connection in a `finally`, the same way `get_price` and `set_price` already do:
```python
def replace_price_list(pool: ConnectionPool, list_id: str, rows: Iterable[tuple[str, int]]) -> int:
    """Atomically replace every price in ``list_id``; returns how many prices were written."""
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

<details><summary>Proof test (add it to the suite)</summary>

```python
import sqlite3

import pytest

from catalog.importer import import_price_list
from catalog.pool import ConnectionPool
from catalog.prices import get_price
from catalog.schema import init_schema


def test_review_proof_failed_imports_exhaust_pool(tmp_path):
    pool = ConnectionPool(str(tmp_path / "c.db"), size=2, timeout=0.1)
    init_schema(pool)
    for _ in range(2):
        with pytest.raises(sqlite3.IntegrityError):
            import_price_list(pool, "eu", "sku,price\nA-1,12.50\nB-2,-1\n")
    assert get_price(pool, "eu", "A-1") is None
```
</details>

## Questions for the author
- None.

## Checked and fine
- Rollback on failure: `with conn:` rolls back the `DELETE` when a row fails; `test_bad_row_keeps_previous_prices` passes and I confirmed the old price survives.
- Other lists are untouched (`DELETE ... WHERE list_id = ?`).
- Price parsing: `Decimal(...) * 100` quantized with `ROUND_HALF_UP` avoids float error; `12.50` becomes `1250`.
- The `CHECK (cents >= 0)` constraint in `catalog/schema.py` rejects negative prices, which is what the test relies on.
- The existing test suite passes (3 tests). Note that none of the existing tests checks the pool after a failure, which is why the leak is not caught.

## Nits
- None worth the author's time beyond the fix above.

Cleanup: the proof file and `__pycache__`/`.pytest_cache` were removed; `git status` is clean.
