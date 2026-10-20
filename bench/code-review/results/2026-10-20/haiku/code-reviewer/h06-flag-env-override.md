## Review Summary

The override idea is useful, and the clamping and non-numeric handling are right. But setting the variable to `0` does not turn a rollout off. The code treats `0` as "no override", so the most important on-call action, cutting a rollout to zero during an incident, silently does nothing. This must be fixed before merge.

### Critical (must fix before merge)

**src/flags.js:13-14** — `const percent = override || flag.rolloutPercent;` The `||` treats `0` as falsy, so `Number("0")` falls back to the configured percentage. I checked on `feature`:

```
config rolloutPercent: 50, FLAG_NEW_CHECKOUT_PERCENT=0
  -> enabled for 480 of 1000 users   (should be 0)
FLAG_NEW_CHECKOUT_PERCENT=0.0 -> same result
```

The PR's stated purpose is letting on-call "dial a rollout up or down". Dialling down to zero is the case that matters most during an incident, and it fails without any error. Only the absence of the variable, or a non-numeric value, should fall back to the configured value.

```javascript
// Before
function effectivePercent(flagName, flag, env) {
  const override = Number(env[overrideVariable(flagName)]);
  const percent = override || flag.rolloutPercent;
  return Math.min(100, Math.max(0, percent));
}

// After
function effectivePercent(flagName, flag, env) {
  const raw = env[overrideVariable(flagName)];
  const override = raw === undefined || raw.trim() === "" ? NaN : Number(raw);
  const percent = Number.isFinite(override) ? override : flag.rolloutPercent;
  return Math.min(100, Math.max(0, percent));
}
```

The empty-string check is needed because `Number("")` is `0`, which would otherwise be read as a real override.

Add tests for `FLAG_NEW_CHECKOUT_PERCENT=0` (nobody enabled, even when the config says 100) and for an empty value (the configured percentage applies). The current tests have no zero case, which is why this was not caught.

### What looks good

Clamping the result to 0-100 after the override is applied, and deriving the variable name in one function (`overrideVariable`) that the tests can check directly, keeps the naming rule in one place.
