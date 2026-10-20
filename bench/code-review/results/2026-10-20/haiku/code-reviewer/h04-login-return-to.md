## Review Summary

The same-site handling is correct, but the check for our other subdomains is an open redirect. The regex in `returnTo.js` is not anchored at the end of the hostname, so an attacker can send users from a login link to any site. This is a security issue and must be fixed before merge. The existing tests pass because none of them try a hostname that starts with our domain.

### Critical (must fix before merge)

**src/returnTo.js:3 and :19** — `TRUSTED_ORIGIN = /^https:\/\/([a-z0-9-]+\.)*example\.com/` matches only a prefix of `url.href`. Nothing stops the match after `example.com`, so hosts an attacker controls pass the check. I confirmed these on `feature`:

```
https://example.com.evil.test/login       -> https://example.com.evil.test/login
https://example.com.phish.io/x            -> https://example.com.phish.io/x
https://app.example.com@evil.test/       -> https://app.example.com@evil.test/
https://docs.example.com.evil.test/      -> https://docs.example.com.evil.test/
```

Each of these is returned as the `location` of the 302 right after a successful login. A phishing email can send a user through the real login page to an attacker's site. The fix is to check the parsed hostname and scheme, not the string:

```javascript
// Before
const TRUSTED_ORIGIN = /^https:\/\/([a-z0-9-]+\.)*example\.com/;
// ...
return TRUSTED_ORIGIN.test(url.href) ? url.href : defaultReturn;

// After
function isTrustedHost(url) {
  return (
    url.protocol === "https:" &&
    !url.username &&
    !url.password &&
    (url.hostname === "example.com" || url.hostname.endsWith(".example.com"))
  );
}
// ...
return isTrustedHost(url) ? url.href : defaultReturn;
```

I checked this version on the cases above. Every attacker host falls back to `/dashboard`. `docs.example.com` and `https://app.example.com/settings` still work as before. Add the four cases above to the `falls back` test. The current list only has `evil.test` and `http://docs...`, so it does not catch this.

### What looks good

Running the target through `new URL(target, appOrigin)` before deciding turns relative paths, protocol-relative URLs like `//evil.test`, and backslash tricks into one canonical form. The `//evil.test` and `/\evil.test` cases are already covered by the test list, which is a good sign that the author thought about open redirects.
