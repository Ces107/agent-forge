# Code review: Send users back to where they were after login

**Verdict: REQUEST CHANGES** · 1 finding (1 proven) · Scope: main..feature, 3 files, 59 insertions, 3 deletions · Tests: `node --test` passed (6 of 6 existing and new); the proof file I added failed as shown below and was removed

The change makes `completeLogin()` honour a `returnTo` parameter via `safeReturnTo()`. Same-site paths
work, and the fallback to `/dashboard` works for the listed bad inputs. But the allow-list for other
subdomains is an unanchored regex, so it trusts any host that merely starts with `example.com`. That
turns the login redirect into an open redirect for phishing, which is the one thing this function must
prevent.

## Findings

### 1. [HIGH · Proven] `src/returnTo.js:3,17`: `TRUSTED_ORIGIN` regex accepts attacker hosts

**What happens:** An attacker sends a user `https://app.example.com/login?returnTo=https://example.com.evil.test/login`
(or `...returnTo=https://app.example.com@evil.test/login`). The user signs in on the real app, and
`completeLogin()` sends a 302 to `evil.test`, a lookalike login page that captures the user's next
credentials. The regex `^https:\/\/([a-z0-9-]+\.)*example\.com` has no end anchor and no host boundary, so
`example.com` matched as a prefix of a longer domain, or inside the userinfo part of the URL, is trusted.

**Evidence:**
```
$ node --test test/review-proof-redirect.test.js
  + 'https://example.com.evil.test/login'
  - '/dashboard'
    actual: 'https://example.com.evil.test/login',
    expected: '/dashboard',
  + 'https://app.example.com@evil.test/login'
    actual: 'https://app.example.com@evil.test/login',
    expected: '/dashboard',
ℹ fail 2
```
`src/returnTo.js` line 17 is `return TRUSTED_ORIGIN.test(url.href) ? url.href : defaultReturn;`, so the
attacker's full href is returned as the redirect target.

**Fix:** compare the parsed hostname, not a regex over the href, and reject credentials in the URL:
```js
function isTrustedHost(url) {
  if (url.protocol !== "https:" || url.username || url.password) return false;
  return url.hostname === "example.com" || url.hostname.endsWith(".example.com");
}

// in safeReturnTo(), replace the last line with:
  return isTrustedHost(url) ? url.href : defaultReturn;
```
and delete the `TRUSTED_ORIGIN` constant.

<details><summary>Proof test (add it to the suite)</summary>

```js
const test = require("node:test");
const assert = require("node:assert");
const { safeReturnTo } = require("../src/returnTo");

test("review proof: example.com as a prefix of an attacker host is not trusted", () => {
  assert.strictEqual(safeReturnTo("https://example.com.evil.test/login"), "/dashboard");
});

test("review proof: example.com in the userinfo part is not trusted", () => {
  assert.strictEqual(safeReturnTo("https://app.example.com@evil.test/login"), "/dashboard");
});
```
</details>

## Questions for the author
- Are `docs`, `status` and `billing` the only trusted subdomains, or should the list be explicit? The fix above allows every `*.example.com` host, as the current code intends, but an explicit allow-list is safer if the set is known.

## Checked and fine
- Same-site paths are kept with query and hash (`/billing/invoices?page=2#latest`).
- Same-site absolute URLs are reduced to paths, which stops the redirect leaving the app.
- Protocol-relative `//evil.test` and backslash variants (`/\evil.test`) resolve to another origin and fall back to `/dashboard` (WHATWG URL parsing treats `\` as `/` for https).
- `javascript:` and `http://docs.example.com` fall back to `/dashboard`.
- Non-string, empty and over-2048-character inputs fall back to `/dashboard`.
- `completeLogin()` keeps the `Set-Cookie` header unchanged.

## Nits
- None worth the author's time beyond the fix above.

Cleanup: the proof file was removed from the repository; `git status` is clean.
