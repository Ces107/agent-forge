## Review Summary

The expiry check breaks password reset for every token. `issue()` stores a timezone-aware `expires_at`, but `is_expired()` compares it with the naive `datetime.utcnow()`. Python raises `TypeError` on that comparison, so `redeem()` crashes on any valid token. The test passes only because it builds a naive `expires_at` that the real code never produces. Request changes; this is blocking.

### Critical (must fix before merge)

**auth/tokens.py:19-20 (`is_expired`)** — `datetime.utcnow()` returns a naive datetime, while `issue()` (line 16) produces `datetime.now(timezone.utc)`. Confirmed by running it: `redeem(issue(1), token)` raises `TypeError: can't compare offset-naive and offset-aware datetimes`. Because `redeem()` now calls `is_expired()` on every redemption, no reset link works, valid or not.

```python
def is_expired(token: ResetToken) -> bool:
    return datetime.now(timezone.utc) >= token.expires_at
```

**tests/test_expiry.py:5-8** — The test constructs the token with `datetime.utcnow() - timedelta(...)`, which is naive. That is not the type `issue()` returns, so the test cannot see the crash. Build the token with `issue()` after moving its clock, or use an aware datetime:

```python
def test_old_token_is_expired():
    token = ResetToken("v", 1, datetime.now(timezone.utc) - timedelta(minutes=1))
    assert is_expired(token)
```

Also add a test that a token from `issue()` redeems successfully, which would have caught the crash.

### Suggestions (should fix)

**auth/tokens.py:19** — `datetime.utcnow()` is deprecated in current Python (the test run emitted the DeprecationWarning). The aware form above fixes both issues.

### Nitpicks (optional)

None.

### What looks good

The expiry check is placed after the constant-time comparison, so an invalid token is still rejected the same way before any expiry logic runs.
