## Review Summary

The design is good, but there is a serious bug in the case on-call needs most. Setting an override to `0` to switch off a bad rollout during an incident is silently ignored, and the flag stays at its configured percentage. One line fixes it, plus a test.

### Critical (must fix before merge)

**src/flags.js:15** — `override || flag.rolloutPercent` treats a valid override of `0` as "no override", because `0` is falsy. I reproduced this with `rolloutPercent: 100`:

```
FLAG_NEW_CHECKOUT_PERCENT="0"    -> 100% of users still enabled
FLAG_NEW_CHECKOUT_PERCENT="0.0"  -> 100%
FLAG_NEW_CHECKOUT_PERCENT="-1"   -> 0%   (only a negative number works as a kill switch)
```

The point of this PR is letting on-call dial a rollout down with a restart, and 0 is the value they will reach for when a feature is breaking production. They restart, the flag stays fully on, and nothing tells them why. Check for "was an override given and is it numeric" explicitly, instead of relying on truthiness:

```js
function parseOverride(raw) {
  if (raw === undefined || raw.trim() === "") {
    return undefined;
  }
  const value = Number(raw);
  return Number.isNaN(value) ? undefined : value;
}

function effectivePercent(flagName, flag, env) {
  const override = parseOverride(env[overrideVariable(flagName)]);
  return Math.min(100, Math.max(0, override ?? flag.rolloutPercent));
}
```

(An empty value has to be checked separately, because `Number("")` and `Number(" ")` are `0`. Today they fall back to the config only by accident of the same `||`.) Add the missing test:

```js
test("zero switches a rollout off", () => {
  const flags = createFlags({ "new-checkout": { rolloutPercent: 100 } }, { FLAG_NEW_CHECKOUT_PERCENT: "0" });
  assert.strictEqual(share(flags, "new-checkout"), 0);
});
```

### Suggestions (should fix)

**src/flags.js:13-16, 26** — An override that doesn't take effect is invisible. A typo in the value (`"fifty"`) or in the variable name (`FLAG_NEWCHECKOUT_PERCENT`) quietly leaves the configured percentage in place. "A typo cannot break a flag" is good, but during an incident on-call needs to know their change *didn't* apply. The override is also re-read from `process.env` on every `isEnabled` call. That is a native lookup, so it costs something on a hot path, and it is pointless when the PR already says a restart is required. Resolve overrides once in `createFlags` and log the result:

```js
function createFlags(config, env = process.env, log = console) {
  const percents = {};
  for (const [name, flag] of Object.entries(config)) {
    if (typeof flag.rolloutPercent !== "number") continue;
    const variable = overrideVariable(name);
    const override = parseOverride(env[variable]);
    if (env[variable] !== undefined && override === undefined) {
      log.warn(`ignoring ${variable}=${JSON.stringify(env[variable])}: not a number`);
    } else if (override !== undefined) {
      log.info(`${name} rollout overridden to ${override}% by ${variable}`);
    }
    percents[name] = Math.min(100, Math.max(0, override ?? flag.rolloutPercent));
  }
  return {
    isEnabled(flagName, userId) {
      const percent = percents[flagName];
      return percent !== undefined && bucket(flagName, userId) < percent;
    },
  };
}
```

### What looks good

Injecting `env` into `createFlags` (defaulting to `process.env`) keeps the override logic testable without changing global state, and the tests use that well. Exporting `overrideVariable` and testing it directly gives on-call one clear source for the exact variable name.
