# Code review: Paginate the audit log export

**Verdict: REQUEST CHANGES** · 2 findings (2 proven) · Scope: main..feature, 2 files, 23 insertions(+), 1 deletion(-) · Tests: `python -m pytest` passed (2 tests)

The PR adds a `paginate()` helper and a page count, but nothing calls them: the export path is unchanged
and still returns every row at once, so the 20k-row problem in the description is not fixed. The helper
itself also has an off-by-one: `page_count` uses floor division, so the last partial page of rows is
unreachable through the page links the UI is meant to render.

## Findings

### 1. [HIGH · Proven] `audit/export.py:11-17`: the helper is never wired into the export, so nothing is paginated
**What happens:** the export endpoint calls `export_rows(rows)` with all rows → the 20k-row response from
the description is unchanged → the UI never receives a page count, so the paging the PR was meant to add
does not exist for users.

**Evidence:**
```
$ grep -rn "paginate\|page_count\|export_rows" --include=*.py .   (excluding the two files the PR touches)
(no output)
```
Only `audit/export.py` and `tests/test_export.py` reference `paginate`. `export_rows` is still called with
the full row set by nothing in this repository, so the endpoint that serves the export is outside this
repository, or it is not connected. Either way the diff does not reach it.

**Fix:** the caller that serves the export must call the new helper, for example:
```python
items, total_pages = paginate(all_rows, page=int(request.args.get("page", 1)), per_page=500)
return {"items": items, "total_pages": total_pages}
```
If the endpoint lives in another repository, the PR should say so and link it.

### 2. [HIGH · Proven] `audit/export.py:5-8`: `page_count` drops the last partial page
**What happens:** 41 rows with `per_page=20` → `41 // 20 = 2` pages → page 3, which holds row 40, is never
offered as a link → that row cannot be reached through pagination. Any total that is not an exact multiple
of the page size loses its tail.

**Evidence:**
```
$ python -m pytest tests/test_review_proof_pages.py  (proof file, deleted afterwards)
>       assert total_pages == 3
E       assert 2 == 3
1 failed in 1.55s
```
The existing test (`tests/test_export.py:8-12`) uses 40 rows with a page size of 20, an exact multiple,
so it cannot catch this.

**Fix:**
```python
def page_count(total_items: int, per_page: int) -> int:
    if per_page <= 0:
        raise ValueError("per_page must be positive")
    return -(-total_items // per_page)  # ceiling division
```

<details><summary>Proof test (add it to the suite)</summary>

```python
from audit.export import paginate


def test_review_proof_last_partial_page_is_reachable():
    rows = [{"n": i} for i in range(41)]
    _, total_pages = paginate(rows, 1, 20)
    last_items, _ = paginate(rows, total_pages, 20)
    assert total_pages == 3
    assert len(last_items) > 0  # row 40 must be reachable through a page link
```
</details>

## Questions for the author
- Where is the export endpoint? It is not in this repository, and I could not find a caller for `paginate()`.
  Is the wiring in another PR?
- Should the page size be fixed server-side, or may the client choose it? An unbounded `per_page` brings back
  the 20k-row response.

## Checked and fine
- `paginate()` rejects `page < 1` and `page_count` rejects `per_page <= 0` before any slicing
  (`audit/export.py:6-7`, `13-14`).
- The slice `rows[start : start + per_page]` is correct for pages 1 to `total_pages` with an exact multiple
  (the existing test, passing).
- `export_rows` is unchanged, so existing behaviour of that function is preserved.

## Nits
- The docstring says `(items, total_pages)`, but the function name and the PR text use "page count". Pick one
  name and use it everywhere.
