# Code review: Add slugify() for article URLs

**Verdict: APPROVE WITH NOTES** · 1 finding (0 proven, 1 nit at Low) · Scope: main..feature, 3 files, 24 insertions, 2 deletions · Tests: `node --test` passed (3 of 3)

`articleUrl()` now appends a slug to the article id (`/articles/3-hello-world`), and falls back to the id
alone when the title produces an empty slug. The helper `slugify()` strips accents with NFKD, lowercases,
collapses non-alphanumerics to single hyphens and trims them. It does what the PR says for the cases the
PR names (accents, punctuation, repeated separators). The one thing to know is that letters with no
ASCII decomposition are dropped, not transliterated.

## Findings

### 1. [LOW · Traced] `src/slugify.js:6`: letters without an ASCII decomposition become hyphens

**What happens:** `.replace(/[^a-z0-9]+/g, "-")` treats every non-ASCII letter as a separator. `"Straße Østerbro"`
→ `"stra-e-sterbro"`, because `ß` and `Ø` do not decompose under NFKD. Non-Latin titles lose everything: `"東京 guide"`
→ `"guide"`. Nothing breaks (the URL stays valid and the id still resolves), but the slug is ugly and two different
titles can collapse to the same slug. The id is still the key, so this is cosmetic.

**Evidence:** I ran `node -e` against `src/slugify.js`:
```
["stra-e-sterbro","guide"]
```

**Fix:** only if the slugs matter for SEO or readability; transliterate the common cases before the regex:
```js
function slugify(title) {
  return String(title)
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/ß/g, "ss")
    .replace(/ø/gi, "o")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}
```

## Questions for the author
- None.

## Checked and fine
- Accents are removed: `"  Crème  brûlée -- recipe  "` → `"creme-brulee-recipe"` (test passes).
- Punctuation and repeated separators collapse to one hyphen, and leading/trailing hyphens are trimmed.
- A title that slugifies to empty (`""`, `undefined`, all punctuation) falls back to `/articles/3` (`articleUrl({ id: 3 })` test passes).
- The id still leads the path, so existing `/articles/:id` routes keep resolving on the id portion (the repo has no router, so this is by construction, not tested).
- `String(title)` guards against non-string titles.

## Nits
- The `ß`/`Ø` case above is the only nit. It is optional.

Cleanup: no files were created in the repository; `git status` is clean.
