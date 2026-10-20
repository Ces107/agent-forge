## Review Summary

Do not merge yet. Returning `None` is a reasonable contract, but this PR changes `find_user` without updating its one production caller. `users/api.py` still expects `UserNotFound`, so a lookup for a missing user now crashes with a `TypeError` instead of returning a 404. The tests pass (2 passed) only because no test covers `users/api.py`.

### Critical (must fix before merge)

**users/api.py:5-9: missing users now cause a crash instead of a 404.** `get_user_plan` still wraps `find_user` in `try/except UserNotFound`. `find_user` no longer raises, so `user` is `None` and `user["plan"]` on line 9 blows up. I confirmed this on the branch:

```
>>> get_user_plan(1)
(200, {'plan': 'pro'})
>>> get_user_plan(99)
TypeError: 'NoneType' object is not subscriptable
```

Why it matters: any request for an unknown user ID used to get a clean `404 {"error": "user not found"}`. Now it becomes an unhandled exception, most likely a 500. That is a user-visible regression, and it will also add noise to error monitoring. The PR description says callers no longer need `try/except`, but no caller was updated. Update the caller to the new contract:

```python
from users.store import find_user


def get_user_plan(user_id: int) -> tuple[int, dict]:
    user = find_user(user_id)
    if user is None:
        return 404, {"error": "user not found"}
    return 200, {"plan": user["plan"]}
```

And add a test at the API layer so a future contract change cannot slip through again:

```python
from users.api import get_user_plan


def test_plan_for_missing_user_is_404():
    assert get_user_plan(99) == (404, {"error": "user not found"})


def test_plan_for_existing_user():
    assert get_user_plan(1) == (200, {"plan": "pro"})
```

### Suggestions (should fix)

**users/store.py:1-2: `UserNotFound` is now dead code.** Nothing raises it any more. After the fix above, nothing will import it either. Keeping it suggests to readers (and to code outside this repo) that `find_user` can still raise it, and they will write `except UserNotFound` blocks that can never run. Delete it in this PR. If other packages import it, keep it for one release with a deprecation note, so their failures are import errors you can see rather than silent `None` handling.

### What looks good

The new `dict | None` return annotation on `find_user` states the contract honestly. A type checker (mypy/pyright) run on this repo would flag `user["plan"]` in `api.py` as indexing an optional value, so wiring a type check into CI would catch this class of mistake automatically. `_USERS.get(user_id)` is also a clear replacement for the old try/except-KeyError block.
