# Code review: Support partial refunds

**Verdict: BLOCK** · 2 findings (1 proven) · Scope: main..feature, 3 files, +84 −6 ·
Tests: `python -m pytest` passed (14)

The change adds `POST /charges/{id}/refunds` with an amount, so support can refund part of a
charge. Single refunds work, but the cap only compares against the original charge, not against
what has already been refunded, so repeated partial refunds can return more money than was paid.
Fix the cap before merging.

## Findings

### 1. [CRITICAL · Proven] `billing/refunds.py:31`: repeated partial refunds can exceed the charge
**What happens:** a 100.00 charge, refunded 60.00 and then 60.00 again → both succeed, and the
customer receives 120.00 back for a 100.00 payment.

**Evidence:**
```
$ python -m pytest tests/test_review_proof_refund_cap.py -q
F
E   billing.errors.RefundExceedsCharge not raised
1 failed in 0.21s
```
`refunds.py:31` checks `amount > charge.amount_cents`; the sum of earlier refunds
(`refunds_for(charge.id)`, defined at `refunds.py:12` and already used by the receipt) is never
included.

**Fix:**
```python
already = sum(r.amount_cents for r in refunds_for(charge.id))
if amount_cents > charge.amount_cents - already:
    raise RefundExceedsCharge(charge.id)
```

<details><summary>Proof test (add it to the suite)</summary>

```python
def test_review_proof_refund_cap(db):
    charge = make_charge(db, amount_cents=10_000)
    refund(db, charge.id, 6_000)
    with pytest.raises(RefundExceedsCharge):
        refund(db, charge.id, 6_000)
```
</details>

### 2. [HIGH · Traced] `billing/refunds.py:29-40`: two concurrent refunds both pass the cap
**What happens:** two support agents refund the same charge at the same moment → both read the
same "already refunded" total, both pass the check, both call the processor.

**Evidence:** the cap check (`:31`) and the insert (`:38`) run in separate statements with no
transaction or row lock; `processor.refund()` runs at `:35`, before the refund row is written,
so not even a unique constraint could stop the second call. Proving it needs the real processor
sandbox; the race window is visible in the code path above.

**Fix:** lock the charge row (`SELECT ... FOR UPDATE`) and write the refund row inside the same
transaction as the check, then call the processor with an idempotency key derived from the
refund id.

## Questions for the author
- Should refunds on a disputed charge be blocked? `charge.status` can be `disputed`
  (`models.py:44`) and the new route does not look at it.

## Checked and fine
- Authorization: the route uses `require_role("support")` like the other `/charges` routes.
- Amounts are integer cents end to end; no float conversion in the serializer.
- `RefundExceedsCharge` maps to 422 in `errors.py`, consistent with the existing errors.
- The new receipt line uses the existing `format_money()` helper.
