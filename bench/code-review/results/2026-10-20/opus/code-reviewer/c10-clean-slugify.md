## Review Summary

This looks good, ship it. `slugify()` is small and correct for its stated scope (accents, punctuation, repeated separators). `articleUrl()` falls back to the old `/articles/<id>` form whenever a slug can't be built, so URLs never become empty or malformed. All 3 tests pass on Node 24. I also tried edge cases: titles that are only separators, empty or missing titles, Cyrillic and Japanese titles (these fall back to the id-only URL), ligatures and circled digits ("ﬁnal ① café" becomes `final-1-cafe`), and a 400k-character run of separators, which takes about 3 ms (no regex backtracking risk). There is one optional nitpick below.

### Nitpicks (optional)

**src/slugify.js:3-4: letters that NFKD does not decompose are dropped instead of transliterated.** Stripping combining marks after `normalize("NFKD")` handles every Spanish/French/Portuguese accent. But ß, Ł, Æ and Ø have no decomposition, so they fall into the `[^a-z0-9]+` branch. "Straße in Łódź" becomes `stra-e-in-odz` and "Æsir & Øl" becomes `sir-l`. Nothing breaks, because the id still identifies the article, but those slugs look wrong. If titles in German, Polish or the Nordic languages are likely, a small map applied before the strip handles the common cases:

```js
const EXTRA = { ß: "ss", æ: "ae", ø: "o", ł: "l", đ: "d", þ: "th", œ: "oe" };

function slugify(title) {
  return String(title)
    .toLowerCase()
    .replace(/[ßæøłđþœ]/g, (c) => EXTRA[c])
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}
```

### What looks good

`src/articles.js:5` puts the id before the slug and falls back to `/articles/${id}` when the slug is empty. Article lookup therefore never depends on the title, a later title edit doesn't break old links, and titles in scripts the slugifier can't handle (I checked Cyrillic and Japanese) still get a working URL. The tests check exactly those two branches (with a title and without one), as well as the accent and separator case named in the PR.
