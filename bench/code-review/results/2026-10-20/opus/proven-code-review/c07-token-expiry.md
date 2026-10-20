# Code review: Reject expired password-reset tokens

**Verdict: BLOCK** · 1 finding (1 proven) · Scope: main..feature, 2 files, +14 −0 ·
Tests: `python -m pytest` passed (2, with 2 `utcnow()` deprecation warnings)

The change adds `is_expired()` and checks it in `redeem()`, so reset tokens stop working after
an hour. `is_expired()` compares a naive `datetime.utcnow()` with the timezone-aware
`expires_at` that `issue()` creates, and Python refuses that comparison. Every redemption of a
real token with the correct value now raises `TypeError`, valid or expired, so password reset
stops working for every user. The new test passes only because it builds a naive token by hand,
which production never does.

## Findings

### 1. [CRITICAL · Proven] `auth/tokens.py:20`: every valid reset token now crashes `redeem()` with TypeError
**What happens:** a user requests a reset (`issue(7)` sets `expires_at` to
`datetime.now(timezone.utc) + TOKEN_TTL`, which is aware) and presents the correct token →
`redeem()` passes the value check and calls `is_expired()` →
`datetime.utcnow() >= token.expires_at` compares naive with aware and raises `TypeError`.
Nobody can reset a password. Expired tokens also raise `TypeError` instead of the
`PermissionError` callers expect, so they surface as server errors rather than a clean
rejection.

**Evidence:**
```
$ python -m pytest -p no:cacheprovider tests/test_review_proof_expiry.py -q --tb=short
auth\tokens.py:26: in redeem
auth\tokens.py:20: in is_expired
E   TypeError: can't compare offset-naive and offset-aware datetimes
FAILED tests/test_review_proof_expiry.py::test_review_proof_fresh_token_redeems
FAILED tests/test_review_proof_expiry.py::test_review_proof_expired_real_token_rejected
```
Why the suite stays green: `tests/test_expiry.py:7` builds the token by hand with a naive
`datetime.utcnow() - timedelta(minutes=1)`, and `tests/test_tokens.py` only exercises the
wrong-value branch, which raises before `is_expired()` runs. No test redeems a token from
`issue()` with its correct value.

**Fix:** compare aware with aware (this also removes the `utcnow()` deprecation warning). I
checked it: a fresh token redeems to `7`, and an expired one raises
`PermissionError('token expired')`.
```python
def is_expired(token: ResetToken) -> bool:
    return datetime.now(timezone.utc) >= token.expires_at
```
`tests/test_expiry.py` has to change with it, because its naive fixture would then raise
`TypeError` the other way. Build the token with `issue()`, as in the proof tests below.

<details><summary>Proof tests (add them to the suite)</summary>

```python
from datetime import timedelta

import pytest

from auth import tokens
from auth.tokens import issue, redeem


def test_review_proof_fresh_token_redeems():
    token = issue(7)                                  # production factory
    assert redeem(token, token.value) == 7            # a valid, unexpired token must work


def test_review_proof_expired_real_token_rejected():
    token = issue(7)
    token.expires_at -= tokens.TOKEN_TTL + timedelta(minutes=1)
    with pytest.raises(PermissionError):
        redeem(token, token.value)
```
</details>

## Questions for the author
- `redeem()` does not mark a token as used, so a token can be redeemed again until it expires.
  Does the caller (outside this repository) invalidate it after the first reset?

## Checked and fine
- The value check still uses `secrets.compare_digest` and runs before the expiry check, so the
  new branch reveals nothing about tokens with a wrong value.
- Token generation (`secrets.token_urlsafe(16)`) and `TOKEN_TTL` (1 hour) are unchanged.
- Boundary: `>=` treats a token as expired at exactly `expires_at`, which is the safe side.
- Callers: `is_expired` is new. In this repository `redeem` is called only from the tests, and
  its signature and return value (`user_id`) did not change.
