## Review Summary

Do not merge yet. As written, this change breaks every password reset. `is_expired()` compares a naive `datetime.utcnow()` with the timezone-aware `expires_at` that `issue()` sets, and Python refuses that comparison. So `redeem()` now raises `TypeError` for every valid token. The new test passes only because it builds its token by hand with a naive datetime, which `issue()` never does. The fix is a one-line change plus a test that goes through `issue()`.

### Critical (must fix before merge)

**auth/tokens.py:20: `is_expired()` crashes on every real token, so password reset is dead.**

```python
return datetime.utcnow() >= token.expires_at
```

`issue()` (line 16) sets `expires_at = datetime.now(timezone.utc) + TOKEN_TTL`, which is a timezone-aware value. `datetime.utcnow()` returns a naive one. I confirmed this on the branch:

```
>>> t = issue(7); redeem(t, t.value)
TypeError: can't compare offset-naive and offset-aware datetimes
```

Why it matters: any user who presents the correct token gets an unhandled `TypeError` (most likely a 500) instead of a password reset. Callers that catch `PermissionError` will not catch it. Wrong tokens still get the expected `PermissionError`, because the digest check runs first. So the existing `test_wrong_value_rejected` stays green and nothing in the suite reveals the break. `datetime.utcnow()` is also deprecated as of Python 3.12 (pytest prints the `DeprecationWarning`).

Use the same clock as `issue()`:

```python
def is_expired(token: ResetToken) -> bool:
    return datetime.now(timezone.utc) >= token.expires_at
```

**tests/test_expiry.py:7: the test builds a token shape that production never produces.** `ResetToken("v", 1, datetime.utcnow() - ...)` gives a naive `expires_at`, so the test checks a case that cannot occur and misses the one that does. Build tokens with `issue()` (or an aware datetime), and test `redeem()` end to end in both directions:

```python
from datetime import datetime, timedelta, timezone

import pytest

from auth.tokens import ResetToken, is_expired, issue, redeem


def test_old_token_is_expired():
    token = ResetToken("v", 1, datetime.now(timezone.utc) - timedelta(minutes=1))
    assert is_expired(token)


def test_fresh_token_redeems():
    token = issue(7)
    assert redeem(token, token.value) == 7


def test_expired_token_rejected_by_redeem():
    token = issue(7)
    token.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    with pytest.raises(PermissionError, match="expired"):
        redeem(token, token.value)
```

Any of the last two would have caught the crash.

### What looks good

The expiry check in `redeem()` (lines 26-27) runs after the constant-time `secrets.compare_digest`. Someone holding a wrong token learns nothing about whether a real token exists or has expired, and the existing timing-safe comparison is left as it was. Raising `PermissionError` for expiry also keeps one exception type for callers, the same one used for an invalid token.
