## Review Summary

`find_user` now returns `None` for a missing user, but the one caller in this repo still catches `UserNotFound`, which is never raised anymore. A missing user therefore reaches `user["plan"]` and returns a 500-style `TypeError` instead of the 404 the handler promises. The PR says the callers were updated; `users/api.py` was not. Request changes.

### Critical (must fix before merge)

**users/api.py:4-9 (`get_user_plan`)** — The `except UserNotFound` branch is now dead code, and the `None` case is not handled. Confirmed by calling `get_user_plan(99)`: it raises `TypeError: 'NoneType' object is not subscriptable`. The endpoint regresses from a clean 404 to an unhandled error for every unknown id.

```python
from users.store import find_user


def get_user_plan(user_id: int) -> tuple[int, dict]:
    user = find_user(user_id)
    if user is None:
        return 404, {"error": "user not found"}
    return 200, {"plan": user["plan"]}
```

### Suggestions (should fix)

**tests/test_store.py** — The tests cover only `find_user`. Add a test for `get_user_plan(99)` returning `(404, ...)`. That test would have failed on this branch and catches the regression.

**users/store.py** — If other services outside this repo still import `UserNotFound`, they will now get `None` silently. Confirm there are no other callers before merging, or keep the exception and add a separate `try_find_user`. The PR description says "every caller wrapped it", but this repo has only one caller and it was missed.

### Nitpicks (optional)

**users/store.py:8** — `dict | None` in the signature is fine on Python 3.10 and later; if the project supports older versions, use `Optional[dict]`.

### What looks good

The lookup is a plain `dict.get`, which removes the try/except wrapper from the store and makes the function easier to read.
