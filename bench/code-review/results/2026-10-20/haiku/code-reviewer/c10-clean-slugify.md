## Review Summary

This is a small, correct change. `slugify` handles accents, punctuation and repeated separators, and `articleUrl` falls back to the bare id when the title produces no slug. All three tests pass with `node --test`. Nothing here needs fixing before merge.

### Critical (must fix before merge)

None.

### Suggestions (should fix)

**src/articles.js:5** — The new URL form is `/articles/<id>-<slug>`. This repo does not contain the route that parses it, so the PR only proves the generated link. Confirm that the route reads the numeric id from the start of the segment (and not the whole segment) before the links go out, or old-style and new-style links will not resolve.

### Nitpicks (optional)

None worth raising.

### What looks good

The fallback to `/articles/<id>` when the slug is empty (for example for a title made only of symbols) is handled in `articleUrl` and covered by its own test, so the function never emits a trailing dash.
