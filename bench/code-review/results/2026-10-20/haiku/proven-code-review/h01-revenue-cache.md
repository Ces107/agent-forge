# Code review: Cache monthly revenue on the dashboard

**Verdict: BLOCK** · 1 finding (1 proven) · Scope: main..feature, 2 files, 24 insertions, 1 deletion · Tests: `python -m pytest` passed on the branch (3 existing + 1 new), but the new test only covers one tenant

The change caches `ReportService.monthly_revenue()` in a module-level `TTLCache` for 300 s, which
matches the PR's stated goal of cutting the dashboard's repeated SUM. The cache key is
`revenue:{month}` and omits `tenant_id`, so the cache is shared by every tenant in the process.
Tenant B gets tenant A's revenue for up to five minutes. This is a cross-tenant data leak and must
be fixed before merge.

## Findings

### 1. [CRITICAL · Proven] `reports/service.py:23`: cache key ignores the tenant

**What happens:** Tenant `acme` loads the dashboard for `2026-09` → the total is stored under
`revenue:2026-09` in the module-level `_revenue_cache`. Tenant `globex` loads the same month within
five minutes → `ReportService(conn, "globex").monthly_revenue()` finds the key, returns `4200`
(acme's total) instead of `900` (globex's total). `average_order()` is wrong for the same reason.

**Evidence:**
```
$ python -m pytest -q tests/test_review_proof_tenant.py
>       assert ReportService(conn, "globex").monthly_revenue("2026-09") == 900
E       AssertionError: assert 4200 == 900
E        +  where 4200 = monthly_revenue('2026-09')
E        +    where monthly_revenue = <reports.service.ReportService object at ...>.monthly_revenue
FAILED tests/test_review_proof_tenant.py::test_review_proof_tenant_isolation
```

**Fix:**
```python
    def monthly_revenue(self, month: str) -> int:
        key = f"revenue:{self.tenant_id}:{month}"
```

<details><summary>Proof test (add it to the suite)</summary>

```python
from reports.db import connect
from reports.service import ReportService


def test_review_proof_tenant_isolation(tmp_path):
    conn = connect(str(tmp_path / "r.db"))
    conn.execute("INSERT INTO orders (tenant_id, month, amount_cents) VALUES ('acme', '2026-09', 4200)")
    conn.execute("INSERT INTO orders (tenant_id, month, amount_cents) VALUES ('globex', '2026-09', 900)")
    conn.commit()
    assert ReportService(conn, "acme").monthly_revenue("2026-09") == 4200
    assert ReportService(conn, "globex").monthly_revenue("2026-09") == 900
```
</details>

## Questions for the author
- None. The dashboard passes `session["tenant_id"]` (reports/dashboard.py:9), so the tenant is always known at the call site.

## Checked and fine
- `TTLCache` (reports/cache.py): expiry compares against the injected clock and deletes expired entries on read; correct.
- Caching `0` is safe: `get()` returns `None` only for a miss, and the `is not None` check preserves a cached zero.
- `average_order()` now reads a cached total; the division-by-zero guard is unchanged.
- The 5-minute staleness is the trade-off the PR accepts; not reported.
- The existing `tests/test_service.py` cases still pass.

## Nits
- The new test does not exercise a second tenant, which is why the suite stays green. The proof test above should be added.

Cleanup: the proof file and `__pycache__` were removed from the repository.
