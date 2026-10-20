## Review Summary

This is a clean, behaviour-preserving refactor. The VAT arithmetic is now in one helper, `_with_vat`, and both public functions call it with the same inputs they used before, so the outputs are unchanged. The existing tests pass on the branch. Ship it.

### Critical (must fix before merge)

None.

### Suggestions (should fix)

None.

### Nitpicks (optional)

None.

### What looks good

Extracting `_with_vat` removes the duplicated quantize-and-add in `shop/pricing.py`, and the `CENT` constant makes the rounding unit explicit. Public signatures for `line_total` and `shipping_total` are untouched, so callers need no changes.

This looks good, ship it.
