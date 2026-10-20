## Review Summary

The pricing math and the new invoice line are right, but the period filter in `billable_calls` has two bugs that cost money. It drops every call made on the last day of the month, and it buckets events by their local date instead of the UTC date the PR promises. Both would under-bill customers on a real invoice run. Needs fixing before merge; the tests do not cover either case.

### Critical (must fix before merge)

**billing/usage.py:23** — `period.end` is inclusive. `month_period` returns `date(2026, 10, 31)` as the end, and `periods.py` counts days with `(end - start).days + 1`. The filter uses `< period.end`, so an event on the 31st is never counted. I checked: an event at `2026-10-31 12:00 UTC` on `feature` gives 0 billable calls. Every 31-day month loses its last day of usage, and 30-day months lose the 30th.

**billing/usage.py:23** — `event.occurred_at.date()` uses the timezone of the timestamp as given, not UTC. The PR says events are bucketed by UTC date, but a timestamp with a `+02:00` offset is bucketed by its local date. I checked: an event at `2026-10-01 00:30 +02:00` (which is `2026-09-30 22:30 UTC`) is counted in October. This is a real risk because the event store may hold local-time timestamps.

Fix both in one line by converting to UTC and using an inclusive upper bound:

```python
# Before
if event.account_id == account_id and period.start <= event.occurred_at.date() < period.end

# After
day = event.occurred_at.astimezone(timezone.utc).date()
if event.account_id == account_id and period.start <= day <= period.end
```

Add tests for an event at `2026-10-31 23:59 UTC`, an event at `2026-10-01 00:30 +02:00` (must be excluded from October), and an event at `2026-09-30 23:30 -01:00` (must be included, since it is `2026-10-01 00:30 UTC`). The current tests use only UTC timestamps in the middle of the month, so they pass with both bugs in place.

If naive datetimes can reach `UsageEvent`, `astimezone` will treat them as local time. Consider rejecting naive timestamps in `UsageEvent.__post_init__`.

### Suggestions (should fix)

**billing/invoice.py:11-13** — `build_invoice` now takes all usage events for every account and filters them inside `billable_calls`. For a large event table, this loads everything for every invoice run. Pass a per-account, per-period query result instead, once the caller has a data source.

### Nitpicks (optional)

None worth raising beyond the points above.

### What looks good

The rounding of partial thousands is done with integer arithmetic (`(calls * 40 + 999) // 1000`), so there is no floating-point drift. The line is only added when there are calls, so accounts without usage keep their current invoice shape.
