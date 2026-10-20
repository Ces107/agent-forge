## Review Summary

The change is well-scoped, but the rounding in `splitByHeadcount` is wrong. Shares are rounded independently, so they often do not add up to the invoice total. Then `bookInvoice` fails with `UnbalancedJournalError` and the invoice is not booked at all. The tests only use amounts that divide evenly, so they miss this. Needs a fix before merge.

### Critical (must fix before merge)

**src/allocation.js:8-9** — Each share is `Math.round((totalCents * headcount) / headcount)` on its own, so the rounding errors do not cancel out. I reproduced it on `feature`:

```
bookInvoice({ id: "inv_1", totalCents: 10000 }, [
  { id: "a", headcount: 1 }, { id: "b", headcount: 1 }, { id: "c", headcount: 1 },
])
-> UnbalancedJournalError: journal does not balance: debits 9999 != credits 10000
```

`splitByHeadcount(10000, …)` returns 3333 + 3333 + 3333 = 9999. Any invoice that does not split evenly across the teams will fail to post in production. Finance would get an error on the monthly run for ordinary invoices, not just edge cases.

Use the largest-remainder method, so the shares always sum to `totalCents`:

```javascript
// After
function splitByHeadcount(totalCents, teams) {
  const headcount = teams.reduce((sum, team) => sum + team.headcount, 0);
  if (headcount === 0) {
    throw new RangeError("cannot split an invoice across teams with no people");
  }
  const exact = teams.map((team) => (totalCents * team.headcount) / headcount);
  const shares = exact.map((value, i) => ({ team: teams[i].id, cents: Math.floor(value) }));
  let remainder = totalCents - shares.reduce((sum, share) => sum + share.cents, 0);
  const byFraction = exact
    .map((value, i) => i)
    .sort((a, b) => (exact[b] % 1) - (exact[a] % 1));
  for (const i of byFraction) {
    if (remainder === 0) break;
    shares[i].cents += 1;
    remainder -= 1;
  }
  return shares;
}
```

Add a test with 10000 split three ways and assert that the shares sum to 10000.

### Suggestions (should fix)

**src/payables.js:6-8** — Passing `teams` that are all `headcount: 0` throws a `RangeError` from inside `bookInvoice`, after the caller has already decided to allocate. Consider validating the teams at the call site, so the error explains which input is wrong.

### Nitpicks (optional)

None worth raising beyond the points above.

### What looks good

Keeping `bookInvoice(invoice)` unchanged when no teams are given, via the default `teams = []`, means existing callers and the `expenses:unallocated` path behave exactly as before. The journal is still checked for balance by `postJournal`, which is what caught this bug.
