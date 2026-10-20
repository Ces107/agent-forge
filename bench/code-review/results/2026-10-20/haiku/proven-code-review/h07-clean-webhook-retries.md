# Code review: Retry failed webhook deliveries with backoff

**Verdict: APPROVE WITH NOTES** · 1 finding (0 proven, 1 traced) · Scope: main..feature, 3 files, 123 insertions, 13 deletions · Tests: `python -m pytest` passed (10 of 10); the new tests cover retry, give-up, and non-retryable status paths

The change retries timeouts, 408/425/429 and 5xx up to five attempts with capped exponential backoff and
jitter, and gives up at once on other statuses. `DeliveryResult` carries the attempt count to the
dispatcher. I checked the retry loop, the status classification, the backoff arithmetic, the
`Webhook-Id` header on every attempt, and the transport contract (`transport.py` says timeouts raise
`ConnectionError`, which the loop catches). The PR's claims hold. One design concern is worth raising
before merge: the retries sleep inline inside the dispatcher's loop.

## Findings

### 1. [MEDIUM · Traced] `webhooks/dispatcher.py:17` + `webhooks/delivery.py:61`: retries block the whole dispatch loop

**What happens:** `dispatch_pending()` calls `deliver()` for each queued event, in sequence, and `deliver()`
calls `time.sleep` between attempts. For an endpoint that returns 503 for every attempt, one event waits
0.5 + 1 + 2 + 4 = 7.5 s before it is marked failed (about 3.75 s at the minimum jitter). Every other
endpoint's events wait behind it. A receiver that is down for an hour with 200 queued events blocks
delivery to every other receiver for roughly 25 minutes (200 × 7.5 s), where the PR's intent was to
ride out a few-second blip.

**Evidence:** `webhooks/dispatcher.py` loops `for endpoint, event in outbox.pending():` and calls
`result = deliver(transport, endpoint, event)` with no `sleep` argument, so `deliver`'s default
`sleep: Callable[[float], None] = time.sleep` is used. `deliver` sleeps `backoff_delay(...)` after every
failed attempt except the last (`if attempt < max_attempts: sleep(...)`). The tests inject `sleep=` directly
into `deliver()`, so the blocking behaviour is not exercised through `dispatch_pending()`.

**Fix:** this is a design change, not a one-line edit. The retries should not hold the dispatch loop:
re-enqueue the failed attempt with a due time (the outbox would need a `not_before` field, and `pending()`
would skip events not yet due), so `dispatch_pending()` moves on to the next event immediately. Until
that is decided, at least inject the sleep into `dispatch_pending()` so the blocking cost is tested.

## Questions for the author
- Is it intended that one unreachable endpoint delays deliveries to every other endpoint in the same
  dispatch run? If not, the re-enqueue-with-due-time approach above is needed.

## Checked and fine
- Retryable set: `{408, 425, 429, 500, 502, 503, 504}` matches the PR text; `400`, `404` and other 4xx are not retried (`test_does_not_retry_client_errors`).
- Gives up after exactly `max_attempts` (5): four sleeps, `[0.5, 1.0, 2.0, 4.0]` (`test_gives_up_after_max_attempts`).
- Backoff is capped at `max_delay` and jittered in `[0.5, 1.0)` of the step: `backoff_delay(10, 0.5, 30.0, 1.0) == 30.0` and `backoff_delay(1, 0.5, 30.0, 0.0) == 0.25`.
- `ConnectionError` (including timeouts, per the transport contract) is retried with `status = None`, and the last status is reported correctly when the endpoint is unreachable.
- The request body and `Webhook-Id` are identical on every attempt (`test_every_attempt_sends_the_same_request`), so receiver-side deduplication stays valid.
- `max_attempts < 1` raises `ValueError`.
- `dispatch_pending()` still marks delivered and failed correctly; the only caller of `deliver()` is the dispatcher, which now reads `result.delivered`.

## Nits
- None worth the author's time beyond the finding above.

Cleanup: no files were created in the repository; `git status` is clean.
