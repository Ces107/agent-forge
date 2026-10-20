## Review Summary

The `paginate` helper slices correctly, but the page count is wrong whenever the row count is not an exact multiple of `per_page`. For example 41 rows at 20 per page reports 2 pages instead of 3, so the last page is unreachable from the UI links this PR is meant to render. The test uses 40 rows, which divides evenly, so it does not catch this. Request changes.

### Critical (must fix before merge)

**audit/export.py:8 (`page_count`)** — `return total_items // per_page` is floor division. Confirmed: `paginate` over 41 rows with `per_page=20` returns `total_pages == 2`. The third page holding the last row has no link in the UI, so those audit rows cannot be reached through pagination. The count must round up.

```python
def page_count(total_items: int, per_page: int) -> int:
    if per_page <= 0:
        raise ValueError("per_page must be positive")
    return -(-total_items // per_page)
```

### Suggestions (should fix)

**tests/test_export.py** — Add a case with a remainder (for example 41 rows, `per_page=20`, expect 3 pages, and the last page holds 1 item). The existing 40-row test cannot distinguish floor from ceiling. Also add a test for `page_count(0, 20) == 0`.

**audit/export.py** — The PR says the export endpoint "returned 20k rows at once" and that this change fixes it, but nothing in this repository calls `paginate`. Only the helper and its test are added. If the endpoint lives in another repo, say so in the PR description; otherwise the endpoint still returns everything.

### Nitpicks (optional)

**audit/export.py:14-15** — `paginate` calls `export_rows` on the slice, so the row conversion only happens for one page. That is good; a short comment is not needed.

### What looks good

Validating `page >= 1` and `per_page > 0` up front gives clear errors instead of negative slices.
