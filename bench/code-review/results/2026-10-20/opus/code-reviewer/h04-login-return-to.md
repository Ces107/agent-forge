## Review Summary

Serious issues: `safeReturnTo` can be bypassed in two separate ways, so this ships an open redirect on the login page. An attacker can send a real `app.example.com/login?returnTo=...` link that lands freshly signed-in users on a phishing page. The existing tests pass, but they only cover the obvious payloads.

### Critical (must fix before merge)

**src/returnTo.js:3, 18** — `TRUSTED_ORIGIN` is anchored at the start but not at the end of the host, and it is tested against the whole `url.href`. That means it accepts any URL whose text merely *begins* with `https://…example.com`. Each of these passes through unchanged on this branch:

```
https://example.com.evil.test/login    -> https://example.com.evil.test/login
https://example.com@evil.test/login    -> https://example.com@evil.test/login   (userinfo; host is evil.test)
https://docs.example.com@evil.test/    -> https://docs.example.com@evil.test/
https://example.com-evil.test/         -> https://example.com-evil.test/
https://example.community/             -> https://example.community/
```

Compare the parsed `hostname` instead of matching text, and reject URLs that carry credentials. The PR names the three subdomains we trust (docs, status, billing), so an explicit allowlist is safer than "any subdomain of example.com". A wildcard also trusts every forgotten or taken-over subdomain.

```js
const TRUSTED_HOSTS = new Set(["docs.example.com", "status.example.com", "billing.example.com"]);

// ...
if (url.username || url.password) {
  return defaultReturn;
}
// ...
return url.protocol === "https:" && url.port === "" && TRUSTED_HOSTS.has(url.hostname)
  ? url.href
  : defaultReturn;
```

**src/returnTo.js:15-16** — The same-origin branch returns `url.pathname` exactly as parsed, and that path can begin with `//`. Browsers read a `Location: //evil.test/login` header as a protocol-relative URL, so the redirect leaves the site:

```
https://app.example.com//evil.test/login  -> //evil.test/login
/.//evil.test/login                       -> //evil.test/login
```

The tests reject the raw string `//evil.test/login`, but these inputs reach the same output by going through the same-origin branch. Collapse the leading slashes so the result is always a path on our own origin:

```js
if (url.origin === appOrigin) {
  return `/${url.pathname.replace(/^\/+/, "")}${url.search}${url.hash}`;
}
```

Add every payload above to the "falls back to the dashboard" test (the allowlisted subdomains still pass, and the two path cases become `/evil.test/login`), so a future refactor can't bring the bypasses back.

### Suggestions (should fix)

**src/returnTo.js:3** — The trusted domain is hardcoded in a regex, while `appOrigin` and `defaultReturn` already live in `src/config.js`. Put the allowed hosts in `config.js` next to `appOrigin`. Adding a fourth subdomain then means editing one list in one place, and you can't end up with `appOrigin` and the allowlist pointing at different domains, for example on a staging deployment.

### What looks good

Resolving the target with `new URL(target, appOrigin)` and comparing `url.origin`, rather than string-prefix checks on the raw input, is the right base, and it correctly handles the `/\evil.test` backslash trick. Rejecting non-strings also covers `?returnTo=a&returnTo=b` arriving as an array.
