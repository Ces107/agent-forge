# Code review: Let on-call override flag rollouts from the environment

**Verdict: REQUEST CHANGES** · 1 finding (1 proven) · Scope: main..feature, 2 files, 49 insertions, 3 deletions · Tests: `node --test` passes 9 of 9, but no test covers the value `0`

The change reads `FLAG_<NAME>_PERCENT` and uses it in place of the configured rollout. Clamping, the
name derivation, and ignoring non-numeric values all work. But `effectivePercent()` treats the
override as a truthy value (`override || flag.rolloutPercent`). The number `0` is falsy, so
`FLAG_<NAME>_PERCENT=0` is silently ignored and the flag keeps its configured rollout. The one case
on-call most needs, switching a feature off in an incident, does not work.

## Findings

### 1. [HIGH · Proven] `src/flags.js:13`: an override of `0` is ignored

**What happens:** On-call sets `FLAG_NEW_CHECKOUT_PERCENT=0` to turn off a broken feature that is configured
at 50% → `Number("0")` is `0`, `0 || 50` gives `50`, so about half the users keep the broken feature while
on-call believes it is off. The same applies to any rollout that should be pulled to zero.

**Evidence:**
```
$ node --test test/review-proof-zero.test.js
AssertionError [ERR_ASSERTION]: enabled for 480 of 1000 users
```
The flag is configured at `rolloutPercent: 50` and the environment sets `"0"`. `effectivePercent()` returns
the configured value because `override` is `0`.

**Fix:** use the override whenever it parses to a finite number, including `0`:
```js
function effectivePercent(flagName, flag, env) {
  const raw = env[overrideVariable(flagName)];
  const override = raw === undefined || raw.trim() === "" ? NaN : Number(raw);
  const percent = Number.isFinite(override) ? override : flag.rolloutPercent;
  return Math.min(100, Math.max(0, percent));
}
```
I checked this logic in a one-off run: `"0"` → 0, `"20"` → 20, `"fifty"` → configured, `""` → configured, `"250"` → 100, `"-5"` → 0, unset → configured.

<details><summary>Proof test (add it to the suite)</summary>

```js
const test = require("node:test");
const assert = require("node:assert");
const { createFlags } = require("../src/flags");

const users = Array.from({ length: 1000 }, (_, i) => `user-${i}`);

test("FLAG_<NAME>_PERCENT=0 turns the rollout off", () => {
  const flags = createFlags({ "new-checkout": { rolloutPercent: 50 } }, { FLAG_NEW_CHECKOUT_PERCENT: "0" });
  const on = users.filter((u) => flags.isEnabled("new-checkout", u)).length;
  assert.strictEqual(on, 0);
});
```
</details>

## Questions for the author
- None.

## Checked and fine
- The variable name is derived as `FLAG_<UPPER_SNAKE>_PERCENT` and `overrideVariable("new-checkout")` matches the test.
- Values above 100 are clamped to 100 (`250` → everyone enabled).
- Non-numeric values such as `"fifty"` are ignored (`NaN` is falsy), so a typo does not break the flag.
- Without the variable the configured percentage applies, and the existing `test/flags.test.js` passes.
- The environment is read on each `isEnabled()` call through `env` (defaults to `process.env`), so a restart is not needed for the module-level singleton, but the PR says "with a restart"; both work.

## Nits
- None worth the author's time beyond the fix above.

Cleanup: the proof file was removed from the repository; `git status` is clean.
