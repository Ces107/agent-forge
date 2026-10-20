# Code review: Simplify find_user lookups

**Verdict: REQUEST CHANGES** · 1 finding (1 proven) · Scope: main..feature, 2 files, 5 insertions(+), 11 deletions(-) · Tests: `python -m pytest` passed (2 tests, both in `tests/test_store.py`)

`find_user` now returns `None` instead of raising `UserNotFound`. The PR updated the store and its tests,
but not the one caller in the repository, `users/api.py`, which still relies on the exception. A request
for a missing user now crashes with `TypeError` instead of returning 404.

## Findings

### 1. [HIGH · Proven] `users/api.py:6-9`: `get_user_plan` breaks for a missing user
**What happens:** a client requests the plan of user `99`, who does not exist → `find_user(99)` returns `None`
(`users/store.py:8-9`) → the `except UserNotFound` branch at `api.py:7` never runs, because nothing raises
→ `user["plan"]` at `api.py:9` raises `TypeError: 'NoneType' object is not subscriptable` → the unhandled
`TypeError` reaches the web layer (a 500 in most frameworks) instead of the 404 the function returns on `main`. The `except` branch is now dead code.

**Evidence:**
```
$ python -m pytest tests/test_review_proof_api.py  (proof file, deleted afterwards)
>       status, body = get_user_plan(99)
users\api.py:9: TypeError
E       TypeError: 'NoneType' object is not subscriptable
1 failed in 1.88s
```
`users/api.py` is not in the PR diff, and `tests/test_store.py` tests only `find_user`, so the suite is green
with this bug present.

**Fix:** update the caller to the new contract (the simpler option, and it keeps the 404 response):
```python
from users.store import find_user


def get_user_plan(user_id: int) -> tuple[int, dict]:
    user = find_user(user_id)
    if user is None:
        return 404, {"error": "user not found"}
    return 200, {"plan": user["plan"]}
```
Also add a caller-level test so the next change cannot break this silently:
```python
from users.api import get_user_plan

def test_missing_user_returns_404():
    status, body = get_user_plan(99)
    assert status == 404
```

## Questions for the author
- Are there callers outside this repository? `find_user` is exported from `users/store.py`, so any other service
  that imports it and catches `UserNotFound` has the same problem. If the store is shared, consider keeping
  the exception and adding a separate `try_find_user()` that returns `None`.
- Is `UserNotFound` still raised anywhere? After this change nothing in the repository raises it.

## Checked and fine
- The `find_user(1)` happy path still returns the Ada record (`tests/test_store.py:4-5`, passing).
- `dict | None` is valid on the Python version the suite runs on (the run passes).
- The store change itself is correct: `_USERS.get(user_id)` returns `None` for any missing key.

## Nits
- None.
