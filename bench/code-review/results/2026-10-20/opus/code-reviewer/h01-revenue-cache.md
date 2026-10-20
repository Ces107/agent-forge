## Review Summary

Serious issue: this cannot merge as is. The cache is module-level (shared by every `ReportService` and so by every request), but the key is only the month. The first tenant to load a month's dashboard sets the revenue every other tenant sees for the next five minutes. The tests pass because none of them looks at two tenants through the cache.

### Critical (must fix before merge)

**reports/service.py:23** — The cache key `f"revenue:{month}"` does not include `self.tenant_id`, but `_revenue_cache` (line 5) is a module global shared across tenants. Revenue for one tenant gets served to every other tenant for that month. That leaks one customer's financial data to another and shows everyone wrong numbers. I reproduced it through `reports.dashboard.dashboard()` with acme = 100000 and globex = 500 in `2026-09`. After acme loads first, globex's dashboard shows `revenue_cents: 100000` and `average_order_cents: 100000`. The `fx.py` cache this was copied from can use a global key because ECB rates are the same for everyone. Revenue is per tenant.

```python
# Before
key = f"revenue:{month}"

# After
key = f"revenue:{self.tenant_id}:{month}"
```

Please add a regression test that loads two tenants for the same month through the cache, something like:

```python
def test_cache_is_per_tenant(tmp_path):
    conn = connect(str(tmp_path / "r.db"))
    conn.executemany(
        "INSERT INTO orders (tenant_id, month, amount_cents) VALUES (?, ?, ?)",
        [("acme", "2026-10", 1000), ("globex", "2026-10", 7)],
    )
    conn.commit()
    assert ReportService(conn, "acme").monthly_revenue("2026-10") == 1000
    assert ReportService(conn, "globex").monthly_revenue("2026-10") == 7
```

### Suggestions (should fix)

**tests/test_revenue_cache.py / tests/test_service.py** — `_revenue_cache` lives on after each test and is never cleared, so the tests depend on each other. They only pass now because each test happens to use a different month (`2026-07`, `2026-08`, `2026-09`, `2026-01`). The next test that reuses a tenant and month will read a value an earlier test cached and fail depending on run order. An autouse fixture fixes this:

```python
# tests/conftest.py
import pytest
from reports import service

@pytest.fixture(autouse=True)
def _clear_revenue_cache():
    service._revenue_cache._entries.clear()
    yield
```

(Better still, give `TTLCache` a `clear()` method so the fixture doesn't need the private attribute.)

**reports/service.py:35-37** — `average_order` divides the cached revenue by a fresh `order_count`. The PR accepts five minutes of staleness, but the same page will now show numbers that contradict each other: `orders` is current while `revenue_cents` is up to five minutes old, and the average mixes the two. During that window a busy tenant sees the average drop as orders come in. If staleness is acceptable, cache the count under the same key (or one query returning `COUNT(*)` and `SUM(...)` together) so the three figures stay consistent.

### Nitpicks (optional)

**reports/cache.py:13** — Expired entries are only removed when their exact key is read again. With keys per tenant and month, the dict grows with every tenant and month anyone has ever viewed for as long as the process lives. This is fine at today's scale, but consider a size cap or a sweep inside `set()`.

### What looks good

Reusing the existing `TTLCache` rather than adding a new caching dependency keeps things consistent with `fx.py`. The new test changes the database underneath and checks that the cached value is still returned, which is the right way to prove the cache is actually hit.
