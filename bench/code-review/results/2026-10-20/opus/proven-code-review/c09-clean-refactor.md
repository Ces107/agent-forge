# Code review: Extract VAT calculation into a helper

**Verdict: APPROVE** · 0 findings · Scope: main..feature, 1 file, +8 −5 ·
Tests: `python -m pytest` passed (2)

The change moves the VAT formula that `line_total` and `shipping_total` both repeated into a
private `_with_vat(net)` helper, and names the `Decimal("0.01")` quantum `CENT`. The PR says
"no behaviour change", and I confirmed it by running the `main` and `feature` versions side by
side on the same inputs: they gave identical results. No material issues.

## Findings

None.

Evidence for "no behaviour change": a differential run from a scratch script outside the
repository. It loads `shop/pricing.py` from `main` via `git show` and the `feature` version from
the checkout, then compares both functions on value and on `str()` (so the exponent and
trailing zeros match too):
```
$ python -I -B diffcheck_c09.py <repo>
checked=160088 mismatches=0
```
The inputs were 20,011 prices (zero, one cent, negatives, three-decimal prices such as `1.005`
and `0.125`, exponent form `1E+2`, nine-digit amounts, and 20,000 random values at 0–3 decimals)
× quantities `0, 1, 2, 3, 7, 100, -1` for `line_total`, plus every price for `shipping_total`.

## Checked and fine
- Arithmetic is identical. `_with_vat` applies the same steps as before:
  `(net * VAT_RATE).quantize(CENT, rounding=ROUND_HALF_UP)`, then `net + vat`. `CENT` equals the
  old literal `Decimal("0.01")`, and `line_total` still computes `unit_price * quantity` before
  the VAT step.
- Rounding mode (`ROUND_HALF_UP`) and quantum (cents) are unchanged in both paths. Nothing
  became float.
- Public API: `line_total`, `shipping_total` and `VAT_RATE` keep their names, signatures and
  return types. The new names are a private `_with_vat` and an added constant `CENT`, so nothing
  is removed.
- Callers: a repository-wide search finds only `tests/test_pricing.py` importing these functions,
  and its assertions (`36.30`, `6.04`) are unchanged and pass.
- Scope: one file, no tests, config or dependency changes hidden in the diff.
