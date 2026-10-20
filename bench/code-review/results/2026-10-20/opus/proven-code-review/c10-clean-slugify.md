# Code review: Add slugify() for article URLs

**Verdict: APPROVE** · 0 findings · Scope: main..feature, 3 files, +24 −2 ·
Tests: `node --test` passed (3)

The change adds `slugify()` (NFKD normalisation, strip combining accents, lowercase, collapse
everything that is not `a-z0-9` into one `-`, trim the dashes at both ends) and makes
`articleUrl` return `/articles/<id>-<slug>`. When there is no title, or the slug comes out
empty, it falls back to `/articles/<id>`. It does what the PR says for accents, punctuation and
repeated separators, and I found no input that breaks it. No material issues.

## Findings

None.

Evidence from probing the new code with `node -e` against the branch:
```
"Hello, World!"                => "hello-world"
"  Crème  brûlée -- recipe  "  => "creme-brulee-recipe"
"Ünïcödé ÀÉÎÕÜ ñ ç"            => "unicode-aeiou-n-c"
"ﬁnance ① déjà vu"             => "finance-1-deja-vu"
"İstanbul"                     => "istanbul"
"tab\tnew\nline"               => "tab-new-line"
"日本語のタイトル" / "Ελληνικά"   => ""   (articleUrl falls back to /articles/3)
"---" / ""                     => ""   (falls back to /articles/3)
articleUrl({id: 3, title: null})               => /articles/3
articleUrl({id: 3, title: 42})                 => /articles/3-42
articleUrl({id: 3, title: "<script>?#/../x"})  => /articles/3-script-x
400k-character dash-heavy input: 43 ms; 400k alternating "a!": 289 ms (linear, no backtracking)
```

## Questions for the author
- Existing links and bookmarks point to `/articles/<id>`, and a slug changes whenever a title is
  edited. The router is not in this repository: does it resolve an article by the leading id
  alone (so `/articles/3`, `/articles/3-old-title` and `/articles/3-new-title` all work, ideally
  with a redirect to the canonical URL)? If it matches the full path, this change breaks every
  old link.

## Checked and fine
- Output is always URL-safe: only `[a-z0-9-]`, with no leading, trailing or doubled dashes, so
  `/`, `?`, `#`, `..` and HTML characters from a title can never reach the path.
- Empty and missing titles: `article.title || ""` plus the `slug ?` check keeps the old
  `/articles/<id>` form, which `"url without title keeps id only"` covers.
- Non-string titles: `String(title)` handles numbers, and `null`/`undefined` are already caught
  by `|| ""`.
- Regex cost: `[^a-z0-9]+` and `^-+|-+$` run in linear time, and the trim runs after dashes are
  collapsed, so very long or hostile titles stay fast.
- Tests: the original assertion (`articleUrl({ id: 3 }) === "/articles/3"`) is kept under a new
  name, not weakened. The new tests assert exact strings.
- Callers: `articleUrl` keeps its signature. The only consumers in the repository are the tests.

## Nits
- Letters that NFKD does not decompose are dropped instead of transliterated:
  `"Straße in Łódź"` → `"stra-e-in-odz"` (the same happens with `ø`, `æ`, `đ`, `ł`). If German,
  Polish or Nordic titles are expected, map them before the regex:
  `.replace(/ß/g, "ss").replace(/[łŁ]/g, "l").replace(/[øØ]/g, "o").replace(/[æÆ]/g, "ae").replace(/[đĐ]/g, "d")`
  (placed after `.normalize("NFKD")`), with a test such as
  `assert.strictEqual(slugify("Straße in Łódź"), "strasse-in-lodz")`.
