## Review Summary

The approach is sound, but there is a serious bug. Each team's share is rounded on its own, so the shares often don't add up to the invoice total. The ledger then rejects the journal and the invoice can't be booked at all. The tests miss it because every amount in them divides evenly.

### Critical (must fix before merge)

**src/allocation.js:6-9** — `Math.round` on each share does not keep the total. When the shares sum to one cent more or less than `totalCents`, `postJournal` throws `UnbalancedJournalError` (src/ledger.js:25) and `bookInvoice` fails. Reproduced on this branch:

| total | headcounts | shares | sum | result |
|---|---|---|---|---|
| 10000 | 1 / 1 / 1 | 3333 / 3333 / 3333 | 9999 | `UnbalancedJournalError` |
| 12345 | 7 / 5 / 2 | 6173 / 4409 / 1764 | 12346 | `UnbalancedJournalError` |
| 5 | 1 / 1 | 3 / 3 | 6 | `UnbalancedJournalError` |

Across 10,000 random invoice totals split 6/4/3, 15% failed to book. With three equal teams, two thirds fail. Finance will hit this in the first month. Use a largest-remainder split: floor every share, then give the leftover cents one at a time to the teams with the largest remainders. The shares then always sum exactly to the total.

```js
function splitByHeadcount(totalCents, teams) {
  const headcount = teams.reduce((sum, team) => sum + team.headcount, 0);
  if (headcount === 0) {
    throw new RangeError("cannot split an invoice across teams with no people");
  }
  const shares = teams.map((team, index) => {
    const exact = totalCents * team.headcount;
    return { index, team: team.id, cents: Math.floor(exact / headcount), remainder: exact % headcount };
  });
  let leftover = totalCents - shares.reduce((sum, share) => sum + share.cents, 0);
  for (const share of [...shares].sort((a, b) => b.remainder - a.remainder || a.index - b.index)) {
    if (leftover === 0) break;
    share.cents += 1;
    leftover -= 1;
  }
  return shares.map(({ team, cents }) => ({ team, cents }));
}
```

Also add a test where the amount doesn't divide evenly, and assert both the shares and that `bookInvoice` succeeds:

```js
test("uneven split still sums to the invoice total", () => {
  const shares = splitByHeadcount(10000, [
    { id: "eng", headcount: 1 },
    { id: "ops", headcount: 1 },
    { id: "sales", headcount: 1 },
  ]);
  assert.strictEqual(shares.reduce((sum, s) => sum + s.cents, 0), 10000);
});
```

### Suggestions (should fix)

**src/allocation.js:2** — `headcount` is never validated. A missing value (`undefined`) turns the sum into `NaN`. The zero check doesn't catch `NaN`, so the failure shows up later as the ledger error `line expenses:eng has invalid amount NaN`. A negative headcount can make the total come out to zero ("no people" error) or produce a negative share the ledger rejects. Since team lists will come from an HR or finance export, reject bad input up front with an error that names the team:

```js
for (const team of teams) {
  if (!Number.isInteger(team.headcount) || team.headcount < 0) {
    throw new RangeError(`team ${team.id} has invalid headcount ${team.headcount}`);
  }
}
```

### What looks good

Keeping the split in a pure `splitByHeadcount` separate from `bookInvoice` makes the allocation easy to test on its own. Defaulting `teams` to `[]` keeps every existing caller on the original single `expenses:unallocated` line, so nothing changes for invoices that aren't allocated.
