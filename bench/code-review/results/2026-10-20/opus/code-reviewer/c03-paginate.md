## Review Summary

The helper is small and close to right, but `page_count` rounds down. Whenever the row count isn't an exact multiple of `per_page`, the final partial page is missing from the page links, and those audit rows can't be reached from the UI. The test passes only because it uses exactly 40 rows. One fix is needed before merge.

### Critical (must fix before merge)

**audit/export.py:8**: `total_items // per_page` drops the final partial page. I checked it: 41 rows at 20 per page reports `total_pages == 2`, although `paginate(rows, 3, 20)` returns row 40. 19 rows reports `0` pages while page 1 has 19 items. 20,001 rows reports 1,000 pages. The UI renders links from this count, so the trailing rows of every export are unreachable, and for an audit-log export that means records silently missing. Use ceiling division:

```python
def page_count(total_items: int, per_page: int) -> int:
    if per_page <= 0:
        raise ValueError("per_page must be positive")
    return -(-total_items // per_page)  # ceil: 41 rows / 20 -> 3 pages
```

### Suggestions (should fix)

**tests/test_export.py:8-12**: The only test uses 40 rows and 20 per page, an exact multiple, which is the one shape where floor and ceiling agree. Add a remainder case so the rounding is pinned down:

```python
def test_last_partial_page_is_counted():
    rows = [{"n": i} for i in range(41)]
    items, total = paginate(rows, 3, 20)
    assert total == 3
    assert items == [{"n": 40}]
```

**audit/export.py:11-17**: `paginate` slices a list that is already fully in memory, so the endpoint still has to load all 20k rows on every page request. Only the response gets smaller. If the goal also covers the database and memory cost, which the PR description suggests, push the `LIMIT ? OFFSET ?` (or keyset pagination on id/timestamp) into the query and compute the total with `COUNT(*)`. This file doesn't show the caller, so check whether that is already planned.

### What looks good

Validating `per_page <= 0` and `page < 1` up front gives callers a clear `ValueError` instead of a `ZeroDivisionError` or a negative slice that would silently return the wrong rows. Reusing `export_rows` for the page items keeps the row format identical to the unpaginated export.
