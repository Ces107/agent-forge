# Code review: Allocate shared invoices to teams by headcount

**Verdict: REQUEST CHANGES** · 1 finding (1 proven) · Scope: main..feature, 3 files, +71 −5 ·
Tests: `node --test` passed (6)

`bookInvoice(invoice, teams)` now splits the debit side across teams with `splitByHeadcount()`.
Each share is rounded independently with `Math.round`, so the shares often do not add up to the
invoice total; the ledger then rejects the journal as unbalanced and the invoice cannot be booked
at all. The tests pass only because every fixture divides exactly (90000 by 6/3, 12000 by 3/1,
5000 by 4/0). Distribute the remainder before merging.

## Findings

### 1. [HIGH · Proven] `src/allocation.js:8`: rounded shares do not sum to the total, so allocated invoices fail to book
**What happens:** a 100.00 Slack invoice split across three teams of equal headcount →
shares 33.33 + 33.33 + 33.33 = 99.99 against a 100.00 credit → `postJournal` throws
`UnbalancedJournalError` and nothing is booked. The opposite also happens: 100.01 split 2/2 →
50.01 + 50.01 = 100.02. Over 5,000 realistic combinations (totals 10.00–19.99, five headcount
mixes), 1,460 (29%) do not balance, so roughly one in three shared invoices Finance tries to
allocate will be rejected. The ledger guard (`ledger.js:25`) prevents a wrong posting, so the
impact is a failure of the new feature on a common path rather than bad books.

**Evidence:**
```
$ node --test test/review-proof-allocation.test.js
  actual: UnbalancedJournalError: journal does not balance: debits 9999 != credits 10000
  actual: UnbalancedJournalError: journal does not balance: debits 10002 != credits 10001
ℹ pass 0
ℹ fail 2

$ node -e '...splitByHeadcount over cents 1000..1999 x [[1,1,1],[2,1],[3,2,2],[5,3,1],[7,6]]...'
1460/5000 splits do not sum to the invoice total
```

**Fix:** floor every share, then hand the leftover cents to the largest remainders, so the
shares always sum to `totalCents`:
```js
function splitByHeadcount(totalCents, teams) {
  const headcount = teams.reduce((sum, team) => sum + team.headcount, 0);
  if (headcount === 0) {
    throw new RangeError("cannot split an invoice across teams with no people");
  }
  const shares = teams.map((team, index) => {
    const exact = totalCents * team.headcount;
    return { index, team: team.id, cents: Math.floor(exact / headcount), rest: exact % headcount };
  });
  let left = totalCents - shares.reduce((sum, share) => sum + share.cents, 0);
  for (const share of [...shares].sort((a, b) => b.rest - a.rest || a.index - b.index)) {
    if (left === 0) break;
    share.cents += 1;
    left -= 1;
  }
  return shares.map(({ team, cents }) => ({ team, cents }));
}
```

<details><summary>Proof test (add it to the suite)</summary>

```js
const test = require("node:test");
const assert = require("node:assert");
const { bookInvoice } = require("../src/payables");

test("review proof: a three-way equal split still books", () => {
  assert.doesNotThrow(() =>
    bookInvoice({ id: "inv_slack", totalCents: 10000 }, [
      { id: "eng", headcount: 1 },
      { id: "sales", headcount: 1 },
      { id: "ops", headcount: 1 },
    ]),
  );
});

test("review proof: an odd total split in halves still books", () => {
  assert.doesNotThrow(() =>
    bookInvoice({ id: "inv_figma", totalCents: 10001 }, [
      { id: "eng", headcount: 2 },
      { id: "design", headcount: 2 },
    ]),
  );
});
```
</details>

## Checked and fine
- Without `teams` (or with `[]`), `bookInvoice` books exactly the old two lines; the existing
  caller and test are unaffected.
- A split with zero total headcount throws a clear `RangeError` before anything is posted.
- A team with headcount 0 gets a 0-cent line, which `postJournal` accepts (`cents < 0` is the
  only rejected amount).
- `postJournal` validates integer, non-negative amounts and balance before pushing, so the
  rounding bug cannot post a half-written or unbalanced journal.

## Nits
- Add a property-style test to `test/allocation.test.js` that asserts
  `shares.reduce((s, x) => s + x.cents, 0) === totalCents` over a range of totals and headcounts;
  every current fixture divides exactly, which is why the bug was not caught.
