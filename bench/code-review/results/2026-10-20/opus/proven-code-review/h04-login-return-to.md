# Code review: Send users back to where they were after login

**Verdict: BLOCK** · 2 findings (2 proven) · Scope: main..feature, 3 files, +59 −3 ·
Tests: `node --test` passed (6)

`completeLogin(user, query)` now redirects to `safeReturnTo(query.returnTo)`. Same-site paths and
our subdomains work as described, but the guard has two holes that turn the login page into an
open redirect: the subdomain regex is not anchored at the end of the host, and a same-origin URL
whose path starts with `//` is returned as a protocol-relative URL. An attacker can send
`https://app.example.com/login?returnTo=https://example.com.evil.test/login`: the victim signs in
on the real site and is then sent to a look-alike page (classic credential phishing). Check the
parsed hostname against an allowlist and reject `//` paths before merging.

## Findings

### 1. [CRITICAL · Proven] `src/returnTo.js:3,18`: the subdomain regex matches hosts that only start with `example.com`
**What happens:** `TRUSTED_ORIGIN = /^https:\/\/([a-z0-9-]+\.)*example\.com/` has no end anchor
and is tested against the whole `url.href`, so any URL whose text *begins* with
`https://…example.com` passes: `https://example.com.evil.test/…`,
`https://docs.example.com.evil.test/…`, `https://example.community/…`, and
`https://example.com@evil.test/…` (where `example.com` is the userinfo and the host is
`evil.test`). After login the user is redirected to an attacker-controlled host. The PR states
"anything else falls back to /dashboard"; these four do not.

**Evidence:**
```
$ node --test test/review-proof-open-redirect.test.js
https://example.com.evil.test/login -> Location: https://example.com.evil.test/login -> browser lands on example.com.evil.test
https://docs.example.com.evil.test/login -> Location: https://docs.example.com.evil.test/login -> browser lands on docs.example.com.evil.test
https://example.com@evil.test/login -> Location: https://example.com@evil.test/login -> browser lands on evil.test
https://example.community/login -> Location: https://example.community/login -> browser lands on example.community
```

**Fix:** decide on the parsed URL's parts, not on its text:
```js
const TRUSTED_HOSTS = new Set(["docs.example.com", "status.example.com", "billing.example.com"]);
// ...
const trusted =
  url.protocol === "https:" &&
  url.port === "" &&
  url.username === "" &&
  url.password === "" &&
  TRUSTED_HOSTS.has(url.hostname);
return trusted ? url.href : defaultReturn;
```
(If every subdomain really must be allowed, use
`url.hostname === "example.com" || url.hostname.endsWith(".example.com")` instead of the set.)

### 2. [CRITICAL · Proven] `src/returnTo.js:15-16`: a same-origin URL with a `//` path becomes a protocol-relative redirect
**What happens:** `returnTo=https://app.example.com//evil.test/login` (or `/.//evil.test/login`,
which the URL parser normalises to the same path) has origin `https://app.example.com`, so the
same-origin branch returns `url.pathname` unchanged: `//evil.test/login`. As a `Location`
header that is a scheme-relative URL, and the browser goes to `https://evil.test/login`. The
existing test rejects `//evil.test/login` only because that input parses to a different origin;
wrapping it in our own origin gets through.

**Evidence:**
```
$ node --test test/review-proof-open-redirect.test.js
https://app.example.com//evil.test/login -> Location: //evil.test/login -> browser lands on evil.test
/.//evil.test/login -> Location: //evil.test/login -> browser lands on evil.test
ℹ pass 0
ℹ fail 6
```

**Fix:**
```js
if (url.origin === appOrigin) {
  if (url.pathname.startsWith("//")) {
    return defaultReturn;
  }
  return `${url.pathname}${url.search}${url.hash}`;
}
```
With both fixes applied, all six inputs above fall back to `/dashboard`, and every case in
`test/returnTo.test.js` keeps its expected value (checked with a standalone copy of the fixed
function).

<details><summary>Proof test for both findings (add it to the suite)</summary>

```js
const test = require("node:test");
const assert = require("node:assert");
const { completeLogin } = require("../src/login");

for (const returnTo of [
  "https://example.com.evil.test/login",
  "https://docs.example.com.evil.test/login",
  "https://example.com@evil.test/login",
  "https://example.community/login",
  "https://app.example.com//evil.test/login",
  "/.//evil.test/login",
]) {
  test(`review proof: login does not redirect off-site for ${returnTo}`, () => {
    const res = completeLogin({ id: "u_1" }, { returnTo });
    const landing = new URL(res.headers.location, "https://app.example.com");
    assert.ok(landing.host === "example.com" || landing.host.endsWith(".example.com"), landing.host);
  });
}
```
</details>

## Questions for the author
- Are all `*.example.com` subdomains ours and free of user-controlled content (customer
  subdomains, preview deployments, an uploads bucket)? If not, the explicit host list above is
  required, not optional.

## Checked and fine
- `completeLogin` keeps the old behaviour when no `returnTo` is passed (existing test still
  asserts `/dashboard`).
- Backslash tricks (`/\evil.test`), `javascript:` URLs, `http:` subdomains and an empty or
  over-long value all fall back to `/dashboard`.
- Header injection: the returned value is always a serialised `URL` part, so CR/LF and tabs are
  stripped or percent-encoded by the parser before they reach `Location`.
- The session cookie is unchanged (`HttpOnly; Secure; SameSite=Lax`) and scoped to the app host,
  so a redirect target does not receive it.
