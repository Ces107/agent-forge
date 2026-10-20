## Review Summary

This looks good and is ready to ship. `mapLimit` does what it says: at most `limit` calls in flight, results in input order, and no new work started after the first failure. The `generateThumbnails(paths, size)` signature still works through the default concurrency, and the existing tests pass with the new helper (8 of 8 on `feature`).

### Critical (must fix before merge)

None.

### Suggestions (should fix)

None.

### Nitpicks (optional)

None worth raising.

### What looks good

The `failed` flag stops the workers from picking up new items after a rejection, which keeps the failure behaviour the same as the old sequential loop, where the first error stopped the upload. The `test/mapLimit.test.js` cases cover the parts that are easy to get wrong: order when later items finish first, the peak concurrency, the stop after failure, and the empty input.
