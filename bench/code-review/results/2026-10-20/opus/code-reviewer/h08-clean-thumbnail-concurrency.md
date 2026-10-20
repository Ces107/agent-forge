## Review Summary

This looks good, ship it. `mapLimit` is correct: output order is preserved, at most `limit` calls run at once, it stops starting new work after the first failure, and a second failure while calls are still in flight doesn't produce an unhandled rejection (I checked with several bad files in one batch). `generateThumbnails` keeps its contract for existing callers. Only small optional points below.

### Nitpicks (optional)

**src/mapLimit.js:10, 28** — The old `generateThumbnails` looped with `for...of`, so it accepted any iterable. `mapLimit` relies on `items.length` and indexing. If it is given a `Set` (or a generator), `new Array(undefined)` produces `[undefined]` and no worker starts, so `generateThumbnails(new Set(["a.jpg", "b.png"]))` quietly resolves to `[undefined]` instead of two thumbnails or an error. Today's only caller (`upload.js`) passes its `paths` through unchanged, so this only matters if someone upstream hands over a Set. Copying the input at the start keeps the old contract at no cost:

```js
async function mapLimit(iterable, limit, fn) {
  const items = Array.from(iterable);
  // ...
```

**src/thumbnails.js:4** — The limit of 4 applies to each upload, not to the whole process. Five galleries uploading at once means 20 resizes in flight. With a real image library on the libuv threadpool (4 threads by default), that is more queuing than speed-up. Leave it for now, but if uploads overlap in production, a single shared limiter is the next step.

### What looks good

The worker-pool design (`limit` workers pulling from a shared `next` index) is simpler and cheaper than chunking into batches of 4, and it keeps every slot busy when resize times vary. The tests check the properties that matter, not just the happy path: order when later items finish first, a peak concurrency of exactly `limit`, and no new work starting after a failure.
