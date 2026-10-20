# Code review: Bill API usage on monthly invoices

**Verdict: BLOCK** · 3 findings (3 proven) · Scope: main..feature, 3 files, +74 −1 ·
Tests: `python -m pytest` passed (6)

`build_invoice()` now sums an account's usage events in the period and adds an "API usage" line.
Three things disagree with the PR: usage on the last day of every month is never billed
(`BillingPeriod.end` is inclusive but the filter treats it as exclusive), partial thousands are
not rounded up (the code rounds to the next cent instead), and events are bucketed by their own
offset's date, not their UTC date. The first one silently loses revenue every month for every
account; fix it before usage pricing goes live.

## Findings

### 1. [CRITICAL · Proven] `billing/usage.py:23`: usage on the last day of the month is never billed
**What happens:** an account makes 50,000 calls on 31 October → the October invoice has no usage
line, and the November invoice does not pick them up either (it starts on 1 November). Every
month, every account's last-day usage is dropped for good, roughly 1/30 of usage revenue.
`month_period()` builds `BillingPeriod(date(y, m, 1), date(y, m, last_day))` with an **inclusive**
end (`periods.py:22-23`; `days` adds 1, `prorated_cents` and `label()` treat the end as the last
billed day), but `billable_calls` filters with `< period.end`. The PR's test has events on 30 Sep,
1 Oct, 17 Oct and 1 Nov, none on 31 Oct, so it does not see this.

**Evidence:**
```
$ python -m pytest tests/test_review_proof_usage.py -s
october lines: [{'description': 'Team plan', 'cents': 4900}] | november lines: [{'description': 'Team plan', 'cents': 4900}]
E       AssertionError: assert 0 == 50000
```

**Fix:**
```python
if event.account_id == account_id and period.start <= _utc_day(event.occurred_at) <= period.end
```
(see finding 3 for `_utc_day`).

<details><summary>Proof test (add it to the suite)</summary>

```python
from datetime import date, datetime, timezone

from billing.accounts import Account
from billing.invoice import build_invoice
from billing.periods import month_period
from billing.usage import UsageEvent, billable_calls

UTC = timezone.utc
ACCOUNT = Account("acc_1", "team", 4900, date(2026, 1, 15))


def test_review_proof_last_day_of_month_is_billed():
    usage = [UsageEvent("acc_1", datetime(2026, 10, 31, 12, 0, tzinfo=UTC), 50_000)]
    assert billable_calls(usage, "acc_1", month_period(2026, 10)) == 50_000
```
</details>

### 2. [HIGH · Proven] `billing/usage.py:27-28`: partial thousands are not rounded up, as the PR says they are
**What happens:** the PR prices usage at "EUR 0.40 per 1,000 calls, rounding partial thousands
up", so 1 call should cost 0.40 and 2,501 calls 1.20 (three started thousands).
`usage_cents()` instead charges per call and rounds up to the next cent: 1 call → 0.01,
2,501 calls → 1.01. The new test `test_partial_thousands_round_up` asserts the code's numbers
(`usage_cents(1) == 1`, `usage_cents(2501) == 101`), so the tests and the PR describe two
different prices. If the description is the published price, every invoice with a partial
thousand is under-billed by up to 0.39.

**Evidence:**
```
$ python -m pytest tests/test_review_proof_usage.py -s
usage_cents(1) = 1 | usage_cents(2501) = 101
E       assert 1 == 40
```

**Fix** (if the PR text is the price; otherwise correct the PR text and the pricing copy):
```python
def usage_cents(calls: int) -> int:
    started_thousands = -(-calls // 1000)
    return started_thousands * API_CALL_PRICE_CENTS_PER_1000
```
and update `test_partial_thousands_round_up` to `1 → 40`, `2500 → 120`, `2501 → 120`,
`3000 → 120`.

<details><summary>Proof test</summary>

```python
from billing.usage import usage_cents

def test_review_proof_partial_thousands_round_up():
    assert usage_cents(1) == 40
    assert usage_cents(2501) == 120
```
</details>

### 3. [MEDIUM · Proven] `billing/usage.py:23`: events are bucketed by their local date, not their UTC date
**What happens:** `occurred_at.date()` returns the date in whatever offset the datetime carries.
An event stamped `2026-10-01T01:00+02:00` (30 Sep 23:00 UTC) is billed in October instead of
September; a naive datetime is bucketed by its wall-clock date. The PR says "events are bucketed
by their UTC date", which only holds if every producer already stamps UTC. Narrow (only events
near midnight with a non-UTC offset), but it moves money between invoices.

**Evidence:**
```
$ python -m pytest tests/test_review_proof_usage.py -s
september: 0 | october: 1000
E       AssertionError: assert 1000 == 0
```

**Fix:**
```python
from datetime import date, datetime, timezone

def _utc_day(moment: datetime) -> date:
    if moment.tzinfo is None:
        raise ValueError("usage timestamps must be timezone-aware")
    return moment.astimezone(timezone.utc).date()
```

<details><summary>Proof test</summary>

```python
from datetime import datetime, timedelta, timezone
from billing.periods import month_period
from billing.usage import UsageEvent, billable_calls

def test_review_proof_bucketed_by_utc_date():
    madrid = timezone(timedelta(hours=2))
    usage = [UsageEvent("acc_1", datetime(2026, 10, 1, 1, 0, tzinfo=madrid), 1000)]
    assert billable_calls(usage, "acc_1", month_period(2026, 10)) == 0
    assert billable_calls(usage, "acc_1", month_period(2026, 9)) == 1000
```
</details>

## Checked and fine
- Existing callers: `build_invoice(account, period)` still works without usage (default `()`),
  and `tests/test_invoice.py` is unchanged and green.
- Usage is filtered by `account_id`, so another account's events cannot leak onto an invoice.
- Amounts stay integer cents end to end; `total_cents` sums the new line.
- Usage is not prorated by `active_from`, which is right for metered calls.
- `usage` is iterated once, so passing a generator is safe.
