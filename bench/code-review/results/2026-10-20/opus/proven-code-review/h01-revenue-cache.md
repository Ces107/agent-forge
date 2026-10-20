# Code review: Cache monthly revenue on the dashboard

**Verdict: BLOCK** · 3 findings (3 proven) · Scope: main..feature, 2 files, +24 −1 ·
Tests: `python -m pytest` passed (4)

The change puts `ReportService.monthly_revenue()` behind a module-level 5-minute `TTLCache`.
The cache key is only the month, while the query is per tenant, so whichever tenant loads the
dashboard first decides the revenue (and the average order) every other tenant sees for that
month for five minutes. Add the tenant to the key before merging.

## Findings

### 1. [CRITICAL · Proven] `reports/service.py:23`: cache key has no tenant, so tenants see each other's revenue
**What happens:** `acme` opens the dashboard for 2026-10 (revenue 1,000.00); within five
minutes `globex` opens it for the same month → `globex` is shown `acme`'s 1,000.00 revenue
instead of its own 5.00, and its average order is computed from `acme`'s revenue. This is a
cross-tenant data leak on a main page, and every tenant's numbers are wrong for that window.
`ReportService` is built per request from the session's `tenant_id` (`dashboard.py:7`), but
`_revenue_cache` is a module-level singleton (`service.py:5`) shared by all requests, and the key
is `f"revenue:{month}"`.

**Evidence:**
```
$ python -m pytest tests/test_review_proof_tenant_cache.py
>       assert globex["revenue_cents"] == 500          # fails: globex is shown acme's revenue
E       assert 100000 == 500
1 failed in 3.27s
```

**Fix:**
```python
key = f"revenue:{self.tenant_id}:{month}"
```
(If the service can ever be used against more than one database, include that too.)

<details><summary>Proof test (add it to the suite)</summary>

```python
from reports.dashboard import dashboard
from reports.db import connect


def test_review_proof_revenue_cache_is_per_tenant(tmp_path):
    conn = connect(str(tmp_path / "r.db"))
    conn.executemany(
        "INSERT INTO orders (tenant_id, month, amount_cents) VALUES (?, ?, ?)",
        [("acme", "2026-10", 100_000), ("globex", "2026-10", 500)],
    )
    conn.commit()
    acme = dashboard(conn, {"tenant_id": "acme"}, "2026-10")
    globex = dashboard(conn, {"tenant_id": "globex"}, "2026-10")
    assert acme["revenue_cents"] == 100_000
    assert globex["revenue_cents"] == 500
```
</details>

### 2. [MEDIUM · Proven] `reports/service.py:35-37`: average order mixes a cached revenue with a fresh count
**What happens:** a tenant has one 30.00 order and loads the dashboard; a second 30.00 order
arrives; the next load within five minutes shows `orders: 2`, `revenue: 30.00`,
`average_order: 15.00`. The PR accepts a stale revenue, but `average_order()` divides the cached
revenue by `order_count()`, which is never cached, so the average is not stale, it is a number
that was never true (and the page contradicts itself). Same with refunds/deletions in the other
direction.

**Evidence:**
```
$ python -m pytest tests/test_review_proof_more.py -s
{'month': '2026-11', 'orders': 2, 'revenue_cents': 3000, 'average_order_cents': 1500}
E       assert 1500 == 3000
```

**Fix:** cache the count and the sum together from one query, so the page is consistently stale:
```python
def _month_totals(self, month: str) -> tuple[int, int]:
    key = f"totals:{self.tenant_id}:{month}"
    cached = _revenue_cache.get(key)
    if cached is not None:
        return cached
    count, total = self.conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(amount_cents), 0) FROM orders WHERE tenant_id = ? AND month = ?",
        (self.tenant_id, month),
    ).fetchone()
    totals = (int(count), int(total))
    _revenue_cache.set(key, totals)
    return totals
```
and derive `order_count`, `monthly_revenue` and `average_order` from it on the dashboard.

<details><summary>Proof test</summary>

```python
def test_review_proof_average_mixes_cached_and_fresh(tmp_path):
    conn = connect(str(tmp_path / "r.db"))
    conn.execute("INSERT INTO orders (tenant_id, month, amount_cents) VALUES ('initech', '2026-11', 3000)")
    conn.commit()
    dashboard(conn, {"tenant_id": "initech"}, "2026-11")
    conn.execute("INSERT INTO orders (tenant_id, month, amount_cents) VALUES ('initech', '2026-11', 3000)")
    conn.commit()
    view = dashboard(conn, {"tenant_id": "initech"}, "2026-11")
    assert view["average_order_cents"] == 3000
```
</details>

### 3. [LOW · Proven] `reports/cache.py:20-21`: two requests reading an expired entry at once raise `KeyError`
**What happens:** this is existing code, but the PR moves `TTLCache` from the hourly FX lookup onto
every dashboard request. When two threads read the same key right after it expires, both see
the expired entry and both run `del self._entries[key]`; the second raises `KeyError`, which
surfaces as a failed dashboard request. Rare (the window is a few bytecodes), but it now sits on
a hot path.

**Evidence:** forcing both readers past the expiry check with a barrier in the clock:
```
$ python -m pytest tests/test_review_proof_more.py
E       assert ["KeyError('k')"] == []
```

**Fix:**
```python
if self._clock() >= expires_at:
    self._entries.pop(key, None)
    return None
```

<details><summary>Proof test</summary>

```python
import threading
from reports import service

def test_review_proof_concurrent_expiry_raises(monkeypatch):
    cache = service._revenue_cache
    cache.set("k", 1)
    barrier = threading.Barrier(2)
    def clock():
        barrier.wait()
        return float("inf")
    monkeypatch.setattr(cache, "_clock", clock)
    errors = []
    def reader():
        try:
            cache.get("k")
        except Exception as exc:
            errors.append(repr(exc))
    threads = [threading.Thread(target=reader) for _ in range(2)]
    [t.start() for t in threads]; [t.join() for t in threads]
    assert errors == []
```
</details>

## Questions for the author
- Does any write path (refunds, order edits, imports) need to invalidate the cached month? Today
  nothing can, so a corrected order stays wrong on the dashboard for up to five minutes.

## Checked and fine
- The SQL is still parameterised and still filtered by `tenant_id`; only the cache layer is wrong.
- `TTLCache.get()` returns `None` on a miss and the revenue is never `None` (`COALESCE(..., 0)`),
  so a cached `0` is served correctly (`0 is not None`).
- `fx.py` keeps its own `_rates` instance; the two caches cannot collide on keys.
- `dashboard()` is the only caller of `ReportService`; `average_order()` is the only other
  internal caller of `monthly_revenue()`.

## Nits
- The new test only checks that a deleted order is still served from cache; it does not test the
  property that matters (two tenants, same month). Replace or extend it with the proof test above.
- `_revenue_cache` is module-level, so it leaks between tests; the suite only passes because each
  test uses a different month. A fixture that clears `_revenue_cache._entries` would keep tests
  independent.
