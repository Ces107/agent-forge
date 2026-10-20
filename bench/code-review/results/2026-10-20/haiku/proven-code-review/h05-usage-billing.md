# Code review: Bill API usage on monthly invoices

**Verdict: BLOCK** · 3 findings (3 proven) · Scope: main..feature, 3 files, 74 insertions, 1 deletion · Tests: `python -m pytest` passes on the branch, but the existing tests encode two of the three bugs below

The change adds a usage line to `build_invoice()`. The line appears and the total is right for the one
case the tests use (12,000 calls = 480 cents). But the money logic is wrong in two independent ways: the
rounding bills a partial thousand at a fraction of its price, and the period filter drops every event on
the last day of the month. A third, narrower bug buckets offset timestamps by their local date instead of
UTC. Usage billing goes live in November, so these would under-bill customers on every invoice. Fix all
three before merge.

## Findings

### 1. [CRITICAL · Proven] `billing/usage.py:27`: partial thousands are billed at a fraction of the price

**What happens:** Account with 1 API call in the month → `usage_cents(1)` = `(1*40 + 999)//1000` = 1 cent,
instead of one thousand-call block at 40 cents. 2,501 calls → 101 cents instead of 3 blocks = 120 cents.
The formula rounds the total cents up, not the number of thousands, so every invoice is under-billed.
The PR says "rounding partial thousands up", which means charging the whole thousand.

**Evidence:**
```
$ python -m pytest tests/test_review_proof_usage.py
E       assert 1 == 40
E        +  where 1 = usage_cents(1)
E       assert 101 == 120
E        +  where 101 = usage_cents(2501)
FAILED tests/test_review_proof_usage.py::test_review_proof_partial_thousand_is_billed_as_a_thousand
FAILED tests/test_review_proof_usage.py::test_review_proof_two_partial_thousands
```
The existing `tests/test_usage.py::test_partial_thousands_round_up` asserts `usage_cents(1) == 1` and
`usage_cents(2501) == 101`, so it passes while locking in the bug.

**Fix:**
```python
def usage_cents(calls: int) -> int:
    thousands = -(-calls // 1000)  # ceiling division: any partial thousand is a full thousand
    return thousands * API_CALL_PRICE_CENTS_PER_1000
```
and change the test to `usage_cents(1) == 40`, `usage_cents(2501) == 120`.

<details><summary>Proof test (add it to the suite)</summary>

```python
from billing.usage import usage_cents

def test_review_proof_partial_thousand_is_billed_as_a_thousand():
    assert usage_cents(1) == 40

def test_review_proof_two_partial_thousands():
    assert usage_cents(2501) == 120
```
</details>

### 2. [CRITICAL · Proven] `billing/usage.py:17`: usage on the last day of the month is never billed

**What happens:** `month_period()` returns `end` as the last day, inclusive (`date(2026, 10, 31)`). The filter
`event.occurred_at.date() < period.end` excludes that day. A call made on 31 October is counted in no
invoice, so every account loses roughly one day of usage in each month that has a 31st or the last day
of the month.

**Evidence:**
```
$ python -m pytest tests/test_review_proof_lastday.py
E       AssertionError: assert 0 == 1000
E        +  where 0 = billable_calls([UsageEvent(account_id='acc_1', occurred_at=datetime.datetime(2026, 10, 31, 12, 0, tzinfo=datetime.timezone.utc), api_calls=1000)], 'acc_1', BillingPeriod(start=datetime.date(2026, 10, 1), end=datetime.date(2026, 10, 31)))
FAILED tests/test_review_proof_lastday.py::test_review_proof_last_day_is_billed
```

**Fix:**
```python
        if event.account_id == account_id and period.start <= event.occurred_at.date() <= period.end
```

<details><summary>Proof test (add it to the suite)</summary>

```python
from datetime import datetime, timezone

from billing.periods import month_period
from billing.usage import UsageEvent, billable_calls


def test_review_proof_last_day_is_billed():
    events = [UsageEvent("acc_1", datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc), 1000)]
    assert billable_calls(events, "acc_1", month_period(2026, 10)) == 1000
```
</details>

### 3. [MEDIUM · Proven] `billing/usage.py:17`: events are bucketed by their local date, not UTC

**What happens:** The PR says events are "bucketed by their UTC date", but `.date()` returns the date in the
timestamp's own offset. An event at 2026-10-01 00:30 +02:00 is 2026-09-30 22:30 UTC and belongs to September,
but it is billed to October. Narrow in practice, because it needs non-UTC timestamps, but it misbills the
first hours of every month for any caller that passes local times.

**Evidence:**
```
$ python -m pytest tests/test_review_proof_usage.py::test_review_proof_utc_bucketing_for_offset_timestamps
E       AssertionError: assert 0 == 1000
FAILED tests/test_review_proof_usage.py::test_review_proof_utc_bucketing_for_offset_timestamps
```

**Fix:**
```python
        if event.account_id == account_id
        and period.start <= event.occurred_at.astimezone(timezone.utc).date() <= period.end
```
(import `timezone` from `datetime`; naive datetimes should be rejected or documented as UTC.)

## Questions for the author
- Do the usage events arrive as UTC-aware datetimes from the metering pipeline? If they always do, finding 3 is only a guard, not a live bug.

## Checked and fine
- Account and period filtering: `acc_2`'s events and the events on 30 September and 1 November are excluded, as the existing test checks.
- `build_invoice()` without `usage` still produces one line; `test_no_usage_no_line` passes.
- Total is the sum of lines (`5380` in the existing test).
- The PR's stated price (EUR 0.40 per 1,000) matches `API_CALL_PRICE_CENTS_PER_1000 = 40`.

## Nits
- None worth the author's time.

Cleanup: all proof files and `__pycache__`/`.pytest_cache` were removed; `git status` is clean.
