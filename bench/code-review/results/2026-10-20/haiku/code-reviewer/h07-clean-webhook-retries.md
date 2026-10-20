## Review Summary

This looks good and is close to ready to ship. The retry rules match the PR: retryable statuses and connection errors are retried, other statuses stop at once, the attempt count is capped, and every attempt sends the same `Webhook-Id`. The one thing to think about before merge is that the retries now run synchronously in the dispatcher, so a dead endpoint can hold up deliveries to other endpoints.

### Critical (must fix before merge)

None.

### Suggestions (should fix)

**webhooks/dispatcher.py:17** — `dispatch_pending` delivers events one after another in a single loop. A failing endpoint now costs up to about 7.5 seconds per event (the sleeps 0.5 + 1 + 2 + 4), and that is before any transport timeout. Since `pending()` drains the whole outbox, a backlog to one unreachable receiver delays every event queued behind it, including events for healthy endpoints. Before this change each failure cost one attempt. Consider a per-endpoint retry queue, or at least a per-run time budget, so a single bad receiver cannot stall the worker.

### Nitpicks (optional)

None worth raising.

### What looks good

Keeping the retry policy as pure, injectable parameters (`sleep`, `jitter`, `max_attempts`) lets `test_delivery.py` check the exact delay sequence (`[0.5, 1.0]`, `[0.5, 1.0, 2.0, 4.0]`) and the cap without real waiting. The `DeliveryResult` return value gives the dispatcher the attempt count it needs for logging.
