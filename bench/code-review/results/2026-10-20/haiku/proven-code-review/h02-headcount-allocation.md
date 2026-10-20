# Code review: Allocate shared invoices to teams by headcount

**Verdict: REQUEST CHANGES** · 1 finding (1 proven) · Scope: main..feature, 3 files, 71 insertions, 5 deletions · Tests: `node --test` passed on the existing 6 tests; the new proof test failed (shown below)

The change adds `splitByHeadcount()` and an optional `teams` argument to `bookInvoice()`, so a shared
invoice books one expense line per team. The split rounds each team's share independently with
`Math.round`, so the shares do not always add up to the invoice total. `postJournal()` then rejects the
journal as unbalanced, and `bookInvoice()` throws for an invoice that should be bookable. The fix is
a largest-remainder split that always sums to `totalCents`.

## Findings

### 1. [HIGH · Proven] `src/allocation.js:7`: independent rounding makes shares miss the total

**What happens:** An invoice of 100 cents shared by three teams with headcount 1 each → shares are
`round(33.33)` three times = 33 + 33 + 33 = 99 cents → debits 99 vs. the 100-cent payables credit →
`UnbalancedJournalError`, and the invoice is not booked at all. Any split where the fractional parts
round down enough hits this (for example 1 cent across 2 teams gives 1 + 1 = 2).

**Evidence:**
```
$ node --test test/review-proof-rounding.test.js
Error [UnbalancedJournalError]: journal does not balance: debits 99 != credits 100
# fail 1
```
The failure comes from `src/ledger.js`, which enforces `debits !== credits` before posting.

**Fix:** largest-remainder allocation. Give each team the floor of its exact share, then hand the
leftover cents (always fewer than the number of teams) to the teams with the largest fractions.
I checked this version by running it against the cases above: 100 → `[34,33,33]`, 7 over
headcount `1,2,4` → `[1,2,4]`, 1 over `1,1` → `[1,0]`, all summing to the total.
```js
function splitByHeadcount(totalCents, teams) {
  const headcount = teams.reduce((sum, team) => sum + team.headcount, 0);
  if (headcount === 0) {
    throw new RangeError("cannot split an invoice across teams with no people");
  }
  const exact = teams.map((team) => (totalCents * team.headcount) / headcount);
  const shares = exact.map(Math.floor);
  const remainder = totalCents - shares.reduce((a, b) => a + b, 0);
  const order = exact.map((v, i) => [v - shares[i], i]).sort((a, b) => b[0] - a[0]);
  for (const [, i] of order.slice(0, remainder)) shares[i] += 1;
  return teams.map((team, i) => ({ team: team.id, cents: shares[i] }));
}

module.exports = { splitByHeadcount };
```

<details><summary>Proof test (add it to the suite)</summary>

```js
const test = require("node:test");
const assert = require("node:assert");
const { bookInvoice } = require("../src/payables");
const { posted } = require("../src/ledger");

test("review proof: three equal teams split $1.00 and still book", () => {
  const before = posted.length;
  bookInvoice({ id: "inv_round", totalCents: 100 }, [
    { id: "eng", headcount: 1 },
    { id: "ops", headcount: 1 },
    { id: "hr", headcount: 1 },
  ]);
  assert.strictEqual(posted.length, before + 1);
});
```
</details>

## Questions for the author
- When every team has zero headcount, the code throws `RangeError` and the invoice is not booked. Is that the intended behaviour, or should it fall back to `expenses:unallocated`?

## Checked and fine
- The existing `splitByHeadcount` tests (6-to-3 split, zero-headcount team) pass.
- `bookInvoice()` without `teams` still books the single `expenses:unallocated` line; `payables.test.js` passes.
- The allocated path books the expected lines for an exact split (9000 + 3000 = 12000, test passes).
- Zero-headcount teams produce a `0`-cent debit line, which `postJournal` accepts (non-negative integers).

## Nits
- None worth the author's time beyond the fix above.

Cleanup: the proof file was removed from the repository; the repo is back to the `feature` branch with no changes.
