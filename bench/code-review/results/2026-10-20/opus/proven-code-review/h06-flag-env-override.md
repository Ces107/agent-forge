# Code review: Let on-call override flag rollouts from the environment

**Verdict: REQUEST CHANGES** · 1 finding (1 proven) · Scope: main..feature, 2 files, +49 −3 ·
Tests: `node --test` passed (9)

`isEnabled()` now reads `FLAG_<NAME>_PERCENT` and uses it in place of the configured rollout,
clamped to 0..100. Raising a rollout, lowering it to a non-zero value, clamping above 100 and
ignoring non-numbers all work as described. But the override is applied with
`override || flag.rolloutPercent`, and `0` is falsy, so setting a flag to 0 silently keeps the
configured percentage. Turning a misbehaving feature off is the main reason on-call would use
this; fix the zero case before merging.

## Findings

### 1. [HIGH · Proven] `src/flags.js:15`: `FLAG_<NAME>_PERCENT=0` is ignored, so on-call cannot switch a flag off
**What happens:** during an incident on-call sets `FLAG_INVOICE_V2_PERCENT=0` and restarts →
`Number("0")` is `0`, `0 || flag.rolloutPercent` returns the configured `100`, and every user
still gets `invoice-v2`. Nothing reports that the override was dropped, so on-call believes the
feature is off while it is still serving 100% of users (25% for `new-checkout`, 50% for
`bulk-export`). Oddly, `-5` does switch the flag off (it is truthy and then clamped to 0), which
makes the behaviour even harder to predict.

**Evidence:**
```
$ node --test test/review-proof-zero-override.test.js
invoice-v2 with FLAG_INVOICE_V2_PERCENT=0: 1000/1000 users still enabled
    actual: 1000,
    expected: 0,
ℹ fail 1

$ node -e '...createFlags({ x: { rolloutPercent: 25 } }, { FLAG_X_PERCENT: v })...'
"0"      24.2%
"-5"     0%
```

**Fix:** treat "is there a valid number" separately from "what is the number":
```js
function effectivePercent(flagName, flag, env) {
  const raw = (env[overrideVariable(flagName)] ?? "").trim();
  const override = /^-?\d+(\.\d+)?$/.test(raw) ? Number(raw) : NaN;
  const percent = Number.isNaN(override) ? flag.rolloutPercent : override;
  return Math.min(100, Math.max(0, percent));
}
```
With this, `"0"` → 0, `"20"` → 20, `"250"` → 100, `"-5"` → 0, and `"fifty"`, `""`, `"50%"`,
`"0x20"` fall back to the configured value, so all new tests keep passing.

<details><summary>Proof test (add it to the suite)</summary>

```js
const test = require("node:test");
const assert = require("node:assert");
const { createFlags } = require("../src/flags");

test("review proof: FLAG_<NAME>_PERCENT=0 switches a flag off", () => {
  const flags = createFlags({ "invoice-v2": { rolloutPercent: 100 } }, { FLAG_INVOICE_V2_PERCENT: "0" });
  const users = Array.from({ length: 1000 }, (_, i) => `user-${i}`);
  assert.strictEqual(users.filter((u) => flags.isEnabled("invoice-v2", u)).length, 0);
});
```
(The run above set `process.env.FLAG_INVOICE_V2_PERCENT = "0"` and used the module-level
`isEnabled`, i.e. the real deployment path; the version here injects `env` so it does not leak
into other tests.)
</details>

## Checked and fine
- The module-level `isEnabled` reads `process.env` on every call, so the override applies to the
  default flags after a restart as described.
- Unknown flags and flags without a numeric `rolloutPercent` are still off; the override cannot
  enable a flag that is not configured.
- Values above 100 clamp to 100, negatives clamp to 0, `NaN` from non-numeric text falls back to
  the configured percentage.
- `bucket()` is unchanged, so users keep their buckets when a rollout is dialled up or down
  (no one flips between on and off except at the edge).
- `overrideVariable("new-checkout")` gives `FLAG_NEW_CHECKOUT_PERCENT`, matching the PR; all three
  configured flag names produce valid environment variable names.

## Nits
- `Number()` also accepts hex and exponents (`"0x20"` → 32%, `"1e2"` → 100%) and fractions
  (`"0.5"` enables 1% because buckets are integers); the regex in the fix rejects the first two.
  Add a `"0"` case to `test/overrides.test.js` so the kill-switch path stays covered.
