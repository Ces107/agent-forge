## Review Summary

This looks good. The retry loop is correct: it stops on 2xx, gives up at once on statuses it shouldn't retry, never sleeps after the last attempt, and the reported attempt count and last status are right in every branch. Retrying is safe because receivers deduplicate on `Webhook-Id` (docs/webhooks.md). Nothing blocks the merge. One operational point is worth thinking about before a customer endpoint goes down for real.

### Suggestions (should fix)

**webhooks/dispatcher.py:16-17** — The retries now run inline, in order, inside the single dispatch loop, so one dead endpoint holds up delivery to everyone else in the batch. Before this change a dead endpoint cost one request timeout per event. Now it costs five timeouts plus up to 7.5 s of backoff per event (0.5 + 1 + 2 + 4 s). With 100 queued events for one unreachable customer and a 10 s transport timeout, that is about 96 minutes of the worker doing nothing else, and every healthy endpoint waits behind it. A cheap mitigation within this PR is to stop retrying an endpoint for the rest of the batch once it has used up its attempts:

```python
def dispatch_pending(outbox: Outbox, transport: Transport) -> int:
    delivered = 0
    exhausted: set[str] = set()
    for endpoint, event in outbox.pending():
        result = deliver(transport, endpoint, event, max_attempts=1 if endpoint in exhausted else 5)
        if result.delivered:
            exhausted.discard(endpoint)
            outbox.mark_delivered(event.id, endpoint)
            delivered += 1
        else:
            if result.attempts > 1:  # retried and still failed: endpoint looks down
                exhausted.add(endpoint)
            log.warning(...)  # unchanged
            outbox.mark_failed(event.id, endpoint)
    return delivered
```

The longer-term fix is to move retries into the outbox (a `next_attempt_at` column on the Postgres outbox) so the worker never sleeps. That is a bigger change and can come later.

**webhooks/dispatcher.py:22-28** — The warning logs the full endpoint URL. Many webhook receivers authenticate with a secret in the URL itself (Slack- or Zapier-style `/services/T0/B0/<token>` paths, `?token=` query strings), so this now writes customer credentials to the logs on every failure. Log the host only, or the subscription id if the Postgres outbox has one:

```python
from urllib.parse import urlsplit

log.warning(
    "webhook %s to %s failed after %d attempt(s), last status %s",
    event.id,
    urlsplit(endpoint).netloc,
    result.attempts,
    result.status,
)
```

### Nitpicks (optional)

**webhooks/delivery.py:18** — The PR says "5xx", but `RETRYABLE_STATUSES` lists only 500/502/503/504. An explicit list is the better choice (retrying 501 is pointless). If many receivers sit behind Cloudflare, though, its transient 520-524 statuses are worth adding. Either way, make the description match the list.

### What looks good

Injecting `sleep` and `jitter` into `deliver` makes the backoff schedule fully checkable in tests (`sleeps == [0.5, 1.0, 2.0, 4.0]`) with no real waiting. `test_every_attempt_sends_the_same_request` pins down the property that makes retries safe: the body and `Webhook-Id` are identical on every attempt. Encoding the body once, outside the loop, guarantees it.
