# Hunting guide: what to look for in each pass

Concrete patterns, not themes. For each one, the question is: can I point at the line, and can I
show it happening?

## Pass 1: intent

- The PR says "fixes X": run the exact input X against the new code. A fix that does not fix is
  the most common defect in agent-written changes.
- The PR says "no behaviour change": diff the observable behaviour (return values, exceptions,
  status codes, log lines other systems parse, rounding) of the old and new code.
- Scope creep: files changed that the description does not explain (config, CI, lockfiles,
  migrations, feature flags).

## Pass 2: contracts beyond the diff

- A function stops raising and returns `None`/`null`/`-1` (or the reverse): every caller that
  caught the exception or checked the sentinel is now wrong. Search for the function name and for
  the exception class name.
- Return shape changed (field renamed, list becomes iterator/generator, sync becomes async,
  units change from cents to decimal): find every consumer, including serializers, templates and
  other services.
- A default changed (timeout, page size, flag, retry count, TLS verification, sort order).
- Schema or migration changes without matching code changes, or code that assumes a column,
  index or constraint the migration does not create.
- Public API or event payload changes without versioning.

## Pass 3: correctness

| Area | Patterns |
|---|---|
| Boundaries | `<` vs `<=`; floor division where a count needs ceiling; inclusive vs exclusive ranges; first and last element; empty input; a single element |
| Numbers and money | float for currency; rounding direction; splitting an amount whose parts do not sum to the total; integer overflow; division by zero |
| Null and empty | `None`/`null`/`undefined`, `""`, `0`, `[]` treated as "missing" by truthiness (`if (!value)`, `value or default`) |
| Errors | `except Exception: pass` or returning a default; catch-and-log that turns failure into success; `finally` that masks the original error; error mapped to the wrong status |
| Async (JS/TS) | `forEach`/`map` with an async callback not awaited; missing `await`; `Promise.all` leaving partial state on failure; unhandled rejections; async work started in a constructor |
| Async (Python) | blocking calls inside `async def`; un-awaited coroutines; shared mutable state across tasks |
| Time | naive vs aware datetimes; local time vs UTC; DST; month ends; comparing strings of dates; deprecated `utcnow()` |
| Text | encoding assumptions; case folding; Unicode normalisation; regex without anchors; regex catastrophic backtracking |
| Python specifics | mutable default arguments; late-binding closures in loops; `is` for value comparison; shadowed builtins; generator exhausted twice |
| JS specifics | `==` coercion; `sort()` without comparator on numbers; comparator returning a boolean; `this` lost in callbacks; `parseInt` without radix on user input |
| Go specifics | ignored `err`; loop variable captured by goroutines (pre-1.22); nil map writes; `defer` in loops; unclosed response bodies |
| Java specifics | `==` on strings or boxed numbers; mutable statics; unclosed resources outside try-with-resources; `equals` without `hashCode` |

## Pass 4: concurrency and state

- **Check-then-act:** read, decide, write in separate steps without a lock or a constraint: two
  callers both pass the check (duplicate rows, double charges, overselling stock, double
  redemption of a coupon).
- **Idempotency:** a retried request repeats a side effect; the dedupe key is checked but not
  claimed atomically; the side effect (charge, email, webhook) happens before the record that
  guards it is committed.
- **Lost update:** read-modify-write of a counter or balance without `UPDATE ... SET x = x + ?`,
  a row lock or a version check.
- **Transactions:** two writes that must commit together but do not; a network call inside a
  transaction; a transaction left open on an error path.
- **Caches:** keys missing a tenant or user dimension (data leaks across users); invalidation
  missing on the write path; caching errors or empty results.
- **Shared state:** module-level or singleton mutable state touched by concurrent requests.

## Pass 5: security

- **Authorization:** a new route, handler, resolver or job that loads an object by id: does it
  check ownership or role like its siblings do? Compare it to the nearest existing route.
- **Injection:** SQL/NoSQL built with string formatting; shell commands with interpolated input;
  template rendering of user input; `eval`; deserialising untrusted data (pickle, YAML load).
- **Paths and URLs:** user input joined into file paths (`../`); server-side requests to
  user-supplied URLs (SSRF); open redirects.
- **Secrets:** keys, tokens or passwords in code, config, logs, error messages, URLs.
- **Limits:** unbounded page sizes, upload sizes, regexes, recursion, loops over user input.
- **Crypto and transport:** TLS verification disabled; weak hashing for passwords; predictable
  tokens (`random` instead of `secrets`/`crypto.randomBytes`); non-constant-time comparison of
  secrets.

## Pass 6: tests

- An assertion was weakened (`== value` → `is not None`, `toEqual` → `toBeDefined`), a test was
  deleted, skipped or marked expected-to-fail in the same change that touches the code it tested.
- The test mocks the very function whose behaviour changed.
- Fixtures that cannot happen in production: objects built by hand instead of by the production
  factory, an in-memory database where production has concurrency, conveniently round sizes.
- New behaviour with no test at all on its main path or its error path. Name the missing test.
