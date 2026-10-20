# Code review: Paginate the audit log export

**Verdict: REQUEST CHANGES** · 1 finding (1 proven) · Scope: main..feature, 2 files, +23 −1 ·
Tests: `python -m pytest` passed (2)

The change adds `paginate(rows, page, per_page)` and `page_count()` so the UI can render page
links. Slicing is correct, but `page_count()` uses floor division, so whenever the row count is
not an exact multiple of the page size the last, partial page is not counted, and the UI never
links to those rows. With fewer rows than one page it reports zero pages. Use ceiling division.

## Findings

### 1. [HIGH · Proven] `audit/export.py:8`: the last partial page is dropped from the page count
**What happens:** 41 audit rows with 20 per page → `total_pages == 2`, so the UI links pages 1
and 2 and row 41 is never shown. 19 rows → `total_pages == 0`, an export with data but no pages.
20,001 rows (the size the PR mentions) → 1,000 pages and the newest row is unreachable. The
author's test uses 40 rows and 20 per page, the one shape where floor and ceiling agree.

**Evidence:**
```
$ python -m pytest tests/test_review_proof_page_count.py
E       assert 2 == 3
E       assert 0 == 1
E       assert 1000 == 1001
3 failed in 3.09s
```
`return total_items // per_page` (`:8`) rounds down; a page count needs to round up.

**Fix:**
```python
return -(-total_items // per_page)  # ceiling division
```

<details><summary>Proof test (add it to the suite)</summary>

```python
import pytest

from audit.export import paginate


@pytest.mark.parametrize("n_rows, expected_pages", [(41, 3), (19, 1), (20_001, 1001)])
def test_review_proof_last_partial_page_is_counted(n_rows, expected_pages):
    rows = [{"n": i} for i in range(n_rows)]
    _, total_pages = paginate(rows, 1, 20)
    assert total_pages == expected_pages
    last_page_items, _ = paginate(rows, expected_pages, 20)
    assert last_page_items[-1] == {"n": n_rows - 1}  # the newest row is on a linked page
```
</details>

## Questions for the author
- Where do `page` and `per_page` come from in the export endpoint (not in this repository)? If
  `per_page` is a query parameter, it needs an upper bound, or a client can ask for
  `per_page=20000` and get the original 20k-row response back.

## Checked and fine
- Slicing: `start = (page - 1) * per_page` and `rows[start : start + per_page]` return the right
  rows for every page, including the last partial one (the rows are there; only the count is wrong).
- `page < 1` and `per_page <= 0` raise `ValueError` instead of returning wrong slices or dividing
  by zero.
- A page past the end returns an empty list rather than raising.
- `export_rows()` is unchanged, and `paginate()` reuses it, so each page has the same row shape as
  the old export.
- Existing suite: 2 passed.
