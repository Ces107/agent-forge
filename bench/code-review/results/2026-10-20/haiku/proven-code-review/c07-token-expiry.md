# Code review: Reject expired password-reset tokens

**Verdict: BLOCK** · 2 findings (1 proven, 1 traced) · Scope: main..feature, 2 files, 14 insertions(+) · Tests: `python -m pytest` passed (2 tests), but the passing tests never reach the new code path

The PR adds `is_expired()` and calls it in `redeem()`. The check compares a naive `datetime.utcnow()`
against the aware `expires_at` that `issue()` creates, so every correct token now raises `TypeError`
instead of redeeming. Password reset is broken for all users, and the existing tests pass because none of
them redeems a valid token.

## Findings

### 1. [CRITICAL · Proven] `auth/tokens.py:19-20` and `:26`: every valid reset token crashes `redeem()`
**What happens:** a user requests a reset → `issue()` stores `expires_at` as a timezone-aware UTC datetime
(line 16) → the user submits the token → `redeem()` passes the compare at line 24 → `is_expired()` runs
`datetime.utcnow() >= token.expires_at` at line 20, which compares a naive value with an aware one → Python
raises `TypeError` → the reset fails with a 500 for every user, and no password is changed.

**Evidence:**
```
$ python -m pytest tests/test_review_proof_redeem.py -p no:warnings  (proof file, deleted afterwards)
auth\tokens.py:20: TypeError
E       TypeError: can't compare offset-naive and offset-aware datetimes
2 failed in 1.38s
```
The two proof tests are: a freshly issued token redeeming with its own value (`redeem(issue(7), value)`),
and an expired aware token. Both fail with the same `TypeError`.

**Fix:** use an aware clock in `is_expired`, matching `issue()`:
```python
def is_expired(token: ResetToken) -> bool:
    return datetime.now(timezone.utc) >= token.expires_at
```

<details><summary>Proof test (add it to the suite)</summary>

```python
from auth.tokens import issue, redeem


def test_review_proof_valid_token_redeems():
    token = issue(7)
    assert redeem(token, token.value) == 7
```
</details>

### 2. [MEDIUM · Traced] `tests/test_expiry.py:6-8`: the new test builds an input production never produces
**What happens:** `test_old_token_is_expired` constructs its token with `datetime.utcnow()` (naive), which
`issue()` never does → the test only exercises the naive-vs-naive path → it passes on the broken code in
finding 1 and cannot catch the regression. `test_tokens.py:7-9` only checks the wrong-value branch, which
returns before `is_expired` is called, so no existing test redeems a valid token at all.

**Evidence:** `tests/test_expiry.py:7` is `ResetToken("v", 1, datetime.utcnow() - timedelta(minutes=1))`,
and `auth/tokens.py:16` is `datetime.now(timezone.utc) + TOKEN_TTL`. The suite passes (`2 passed`), so the
test is green with the bug present. Traced, not run against production code paths beyond these two lines.

**Fix:** build the token with the production factory, and add the valid-redeem case from finding 1:
```python
from auth.tokens import issue, redeem

def test_expired_token_rejected(monkeypatch):
    token = issue(7)
    token.expires_at = token.expires_at - timedelta(hours=2)   # now in the past, aware
    with pytest.raises(PermissionError):
        redeem(token, token.value)
```

## Questions for the author
- Are reset tokens single-use? `redeem()` does not mark a token as consumed, so a token can be redeemed
  repeatedly until it expires. That predates this PR, but the title says "reject expired" only.
- Is `utcnow()` deprecated for this project's Python version? The run reports DeprecationWarnings from it.

## Checked and fine
- The compare runs before the expiry check, so a wrong value is still rejected with `PermissionError`
  (`tests/test_tokens.py:7-9`, passing).
- `secrets.compare_digest` and `secrets.token_urlsafe(16)` are unchanged, and the token entropy is adequate.

## Nits
- None beyond the findings above.
