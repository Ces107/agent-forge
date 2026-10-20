# Code review: Extract VAT calculation into a helper

**Verdict: APPROVE** · 0 findings · Scope: main..feature, 1 file (`shop/pricing.py`), 8 insertions(+), 5 deletions(-) · Tests: `python -m pytest` passed (2 tests)

The PR moves the duplicated VAT maths into `_with_vat()` and makes `line_total` and `shipping_total`
call it. The claim is "no behaviour change", and that holds: I compared the `main` and feature versions
over 200,000 random price and quantity pairs plus half-cent boundary values, and found zero mismatches.

## Findings

None.

## Questions for the author
- None.

## Checked and fine
- Behaviour equivalence: `main` and feature `line_total` and `shipping_total` returned identical `Decimal`
  values for 200,000 random inputs (prices from 0.00 to 999.99, quantities 0 to 50) and for half-cent
  boundaries (`0.005`, `0.015`, `0.025`, `1000000.005`). Check run from the scratchpad against
  `git show main:shop/pricing.py`, outside the repository.
- Rounding is unchanged: `_with_vat` uses the same `ROUND_HALF_UP` quantize to `CENT` (0.01) that both
  call sites used before.
- Return types are unchanged (`Decimal`), so callers see no difference. There are no other callers in the
  repository beyond `tests/test_pricing.py`.
- Existing tests pass: `line_total(10.00, 3) == 36.30`, `shipping_total(4.99) == 6.04`.

## Nits
- None.
