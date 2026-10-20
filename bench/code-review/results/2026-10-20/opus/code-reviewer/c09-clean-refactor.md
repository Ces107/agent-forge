## Review Summary

This looks good, ship it. The PR really is a pure refactor: `_with_vat` does exactly the work that `line_total` and `shipping_total` each used to do inline, with the same precision, the same `ROUND_HALF_UP` rounding and the same quantize-the-VAT-then-add order. To check, I compared the `main` and `feature` versions of both functions on 200,000 random `Decimal` prices and quantities (including negatives and up to 4 decimal places). The results were identical, including their string form, so trailing-zero precision is unchanged too. The existing tests pass (2 passed).

No critical issues, suggestions or nitpicks.

### What looks good

The VAT step now lives in one place (`shop/pricing.py:7-9`). A future rate or rounding-rule change cannot be applied to product lines and forgotten for shipping, which was the real risk of having two copies. Pulling `Decimal("0.01")` into the named constant `CENT` means the precision is visible at module level next to `VAT_RATE`, instead of being repeated as a literal. Keeping the helper private (`_with_vat`) leaves the module's public API unchanged.
