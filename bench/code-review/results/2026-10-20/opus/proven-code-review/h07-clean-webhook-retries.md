# Code review: Retry failed webhook deliveries with backoff

**Verdict: APPROVE** · 0 findings · Scope: main..feature, 3 files, +123 −13 ·
Tests: `python -m pytest` passed (10)

`deliver()` now makes up to five attempts, retrying connection errors (timeouts included, per the
`Transport` contract) and 408/425/429/500/502/503/504 with capped exponential backoff and jitter,
and stops at once on any other status. It returns a `DeliveryResult`, and the only caller,
`dispatch_pending()`, was updated to read `.delivered` and log attempts and last status. The
change does what the PR says; I found nothing that needs fixing before merge, only two points
worth confirming below.

## Questions for the author
- The PR says "5xx", but `RETRYABLE_STATUSES` is a fixed list: 501, 505-511 and the
  proxy codes many endpoints sit behind (Cloudflare 520-524, e.g. 522 "connection timed out",
  524 "timeout") are given up after one attempt (`deliver(..., [522])` →
  `DeliveryResult(delivered=False, attempts=1, status=522)`). Is that intended? If not,
  `status >= 500 and status != 501` covers the blips the PR is aimed at.
- The dispatcher delivers sequentially in one worker. An endpoint that is down now costs up to
  5 transport timeouts plus 7.5 s of backoff (3.75–7.5 s with jitter) per queued event, so
  N events to one dead endpoint delay every other subscriber in the same batch by N times that.
  Is that acceptable for the worker's batch size, or should repeated failures to one endpoint
  short-circuit the rest of its events in the batch?

## Checked and fine
- Callers: `dispatch_pending()` is the only caller of `deliver()` in the repository and now
  uses `result.delivered`; nothing else relies on the old `bool` (a `DeliveryResult` is always
  truthy, so an un-updated caller would have been wrong; there is none).
- Retry set: 408, 425, 429 and 504 are retried and succeed on the second attempt; 404 and 302
  stop after one attempt (checked with a one-off script against `deliver()`).
- Attempt accounting: sleeps happen only between attempts (none after the last); exhausting
  attempts returns `attempts == max_attempts` and the last status, or `None` when the last
  attempt was a connection error. `max_attempts < 1` raises `ValueError`.
- Backoff: `min(max_delay, base * 2**(attempt-1)) * (0.5 + jitter/2)` gives 0.5/1/2/4 s at full
  jitter and never exceeds `max_delay`; `random.random()` is in [0, 1) as the docstring says.
- Idempotency: the body and headers are built once, so every attempt sends the same bytes and
  the same `Webhook-Id`; `docs/webhooks.md` already tells receivers deliveries are
  at-least-once and to deduplicate on that header, so retrying is safe.
- Tests: the two removed tests (`is True` on 2xx, `is False` on 500) are replaced by stronger
  ones on the new result type; no assertion was weakened. The dispatcher test still covers
  delivered and failed outcomes (404 is not retried, so it consumes one response).

## Nits
- `dispatcher.py:22-28` logs the full endpoint URL at WARNING. If subscribers can register URLs
  that carry a credential (Slack/Discord-style hook paths, `?token=` query strings), log
  `urlsplit(endpoint)._replace(query="", fragment="").geturl()` or the subscription id instead.
