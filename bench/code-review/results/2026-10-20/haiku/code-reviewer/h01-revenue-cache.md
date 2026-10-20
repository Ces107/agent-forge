## Review Summary

The caching idea is reasonable, but the cache key leaves out the tenant, so one tenant's revenue is served to another. This is a data leak in a multi-tenant dashboard and must be fixed before merge. The new test does not catch it because it only uses one tenant.

### Critical (must fix before merge)

**reports/service.py:23** — `key = f"revenue:{month}"` does not include `self.tenant_id`, and `_revenue_cache` is a module-level global shared by every `ReportService` instance. The first tenant to load a month fills the entry, and every other tenant gets that value for five minutes. I reproduced it on the `feature` branch with two tenants in the same month:

```
acme    2026-09 -> 4200    (correct)
globex  2026-09 -> 4200    (should be 99900)
```

Dashboards show other customers' revenue, and `average_order` mixes the two tenants' figures.

```python
# Before
key = f"revenue:{month}"

# After
key = f"revenue:{self.tenant_id}:{month}"
```

Add a test that creates two tenants with different totals for the same month and asserts that each one gets its own value.

### Suggestions (should fix)

**reports/service.py:5** — `_revenue_cache` is a process-wide singleton with no size bound. Keys grow with every tenant and month, and entries that expire are only removed when someone reads them again. Keep the cache per instance or per app, or bound it, so memory does not grow without limit over time.

**reports/service.py:33** — `average_order` calls `order_count` (not cached) and `monthly_revenue` (cached). The two values can come from different moments, so the average can be inconsistent for up to five minutes. Either cache both or document that the average is only approximate.

### Nitpicks (optional)

- The `if cached is not None` check will treat a cached `None` as a miss. This is harmless for an int total, but worth knowing if the cache is reused.

### What looks good

The cache lives behind the existing `TTLCache` with an injectable clock, so the expiry behaviour is testable without sleeping. The cache is only consulted in `monthly_revenue`, which keeps the change small.
