# Code review: Simplify find_user lookups

**Verdict: REQUEST CHANGES** · 1 finding (1 proven) · Scope: main..feature, 2 files, +5 −11 ·
Tests: `python -m pytest` passed (2)

The change makes `find_user()` return `None` for a missing user instead of raising
`UserNotFound`, and updates its unit test to match. The PR says callers no longer need
try/except, but the only real caller, `users/api.py`, was not changed. It still waits for an
exception that is never raised, so a request for an unknown user now crashes instead of
returning 404.

## Findings

### 1. [HIGH · Proven] `users/api.py:5-9` (unchanged caller): an unknown user now crashes with TypeError instead of returning 404
**What happens:** `get_user_plan(99)` calls `find_user(99)`, which now returns `None` without
raising. The `except UserNotFound:` branch at `api.py:7` can no longer run, so execution reaches
`user["plan"]` at `api.py:9` and raises
`TypeError: 'NoneType' object is not subscriptable`. A client asking for a user that does not
exist gets a server error instead of `404 {"error": "user not found"}`.

**Evidence:**
```
$ python -m pytest -p no:cacheprovider tests/test_review_proof_find_user.py -q --tb=short
users\api.py:9: in get_user_plan
E   TypeError: 'NoneType' object is not subscriptable
FAILED tests/test_review_proof_find_user.py::test_review_proof_missing_user_is_404
```
`git diff main...feature` touches only `users/store.py` and `tests/test_store.py`, and
`users/api.py` is identical on both branches. The suite stays green because the deleted test
`test_missing_raises` was the only one covering the missing-user path, and nothing tests
`get_user_plan`.

**Fix** (I checked it: returns `(404, {'error': 'user not found'})` for 99 and
`(200, {'plan': 'pro'})` for 1):
```python
from users.store import find_user


def get_user_plan(user_id: int) -> tuple[int, dict]:
    user = find_user(user_id)
    if user is None:
        return 404, {"error": "user not found"}
    return 200, {"plan": user["plan"]}
```
`UserNotFound` is now never raised anywhere. Delete it with the import, or keep it only if code
outside this repository still imports it.

<details><summary>Proof test (add it to the suite)</summary>

```python
from users.api import get_user_plan


def test_review_proof_missing_user_is_404():
    assert get_user_plan(99) == (404, {"error": "user not found"})


def test_review_proof_existing_user_plan():
    assert get_user_plan(1) == (200, {"plan": "pro"})
```
</details>

## Questions for the author
- Is `users.store.find_user` or `UserNotFound` imported by other services or packages outside
  this repository? Any such caller has the same problem. If there are some, changing the
  contract needs a deprecation step rather than a silent switch.

## Checked and fine
- `find_user` for an existing id still returns the same dict object as before (the
  `test_find_existing` assertion is unchanged and passes, and so does `get_user_plan(1)`).
- Callers: a repository-wide search for `find_user`, `UserNotFound` and `get_user_plan` finds
  only `users/api.py` and the tests.
- The replaced test (`test_missing_raises` → `test_missing_returns_none`) matches the new
  contract and is not a weakened assertion. The gap is the missing caller test above.
- `dict | None` in the annotation needs Python 3.10+, which the environment has (3.14).
