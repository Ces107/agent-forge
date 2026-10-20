## Review Summary

Serious issues before this goes live in November. The period filter drops the last day of every month, so that usage is never billed in any month. The rounding doesn't match the pricing described in the PR. And events aren't actually bucketed by UTC date. The tests pass because none of them puts an event on the last day of the month, and the rounding test asserts the implementation's numbers rather than the stated pricing.

### Critical (must fix before merge)

**billing/usage.py:23** — `BillingPeriod.end` is the *last day* of the period, inclusive. `month_period` sets it to `date(year, month, last_day)`, `days` adds `+ 1`, `prorated_cents` treats it as inclusive, and the label reads `01 Oct 2026 - 31 Oct 2026`. The new filter uses `< period.end`, so events on the 31st are excluded from October. November starts on the 1st, so they are never billed at all. I reproduced it: 10,000 calls at `2026-10-31 12:00 UTC` bill 0 in October and 0 in November. That is roughly 1/30 of every customer's usage lost every month. The test only checks exclusion with an event on `2026-11-01`, which is why it passes.

```python
# Before
if event.account_id == account_id and period.start <= event.occurred_at.date() < period.end

# After
if event.account_id == account_id and period.start <= _utc_date(event.occurred_at) <= period.end
```

Add an event on `datetime(2026, 10, 31, 23, 59, tzinfo=UTC)` to `test_counts_only_this_accounts_events_in_the_period` so the inclusive end is pinned down.

**billing/usage.py:27-28** — The PR says "EUR 0.40 per 1,000 calls, rounding partial thousands up", meaning a started thousand is billed as a full thousand. The code instead prices each call at 0.04 cents and rounds the *cents* up:

| calls | code | per started thousand |
|---|---|---|
| 1 | 1 | 40 |
| 1001 | 41 | 80 |
| 2501 | 101 | 120 |

`test_partial_thousands_round_up` asserts the code's numbers (`usage_cents(2501) == 101`), so the test name and the PR describe one pricing rule and the assertions encode another. One of them is wrong, and it decides what goes on every customer's invoice. Please confirm with whoever owns pricing before merging. If the PR text is the rule:

```python
def usage_cents(calls: int) -> int:
    thousands = -(-calls // 1000)  # ceil
    return thousands * API_CALL_PRICE_CENTS_PER_1000
```

with the test updated to `usage_cents(1) == 40`, `usage_cents(2500) == 120`, `usage_cents(2501) == 120`. If the code is the rule, fix the PR text and rename the test, for example to `test_usage_rounds_up_to_the_cent`, so nobody "fixes" it later.

### Suggestions (should fix)

**billing/usage.py:23** — `event.occurred_at.date()` returns the date in whatever timezone the datetime carries, not UTC. An event recorded as `2026-11-01 00:30+01:00` (Madrid) happened at `2026-10-31 23:30 UTC`, but it is billed in November (reproduced). A naive datetime is silently treated as if it were already UTC. To bucket by UTC date as the PR says, convert first, and refuse naive timestamps so a bad producer can't move usage between months:

```python
from datetime import date, datetime, timezone

def _utc_date(moment: datetime) -> date:
    if moment.tzinfo is None:
        raise ValueError("usage events need a timezone-aware occurred_at")
    return moment.astimezone(timezone.utc).date()
```

### What looks good

Integer-cent arithmetic all the way through (`API_CALL_PRICE_CENTS_PER_1000` and floor-division ceiling) avoids float rounding on money. Leaving out the usage line when there were no calls keeps invoices for plan-only accounts unchanged.
