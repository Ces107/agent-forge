# How agent-written changes usually go wrong

Coding agents optimise for "tests green, task looks done". These are the tells. Check every one
on any change that was written or heavily edited by an AI assistant.

1. **The fix that does not fix.** The PR claims a bug is fixed, but the original failing input
   still fails. Run it.
2. **Green by weakening.** An assertion loosened, a test deleted, skipped or marked expected-to-fail,
   a snapshot regenerated, or a coverage threshold lowered in the same change.
3. **Swallowed failure.** A new `try/except` or `try/catch` that returns a default (`0`, `""`,
   `[]`, `null`, `false`) or only logs. The error disappears and bad data flows on.
4. **Silent fallback.** "If the config/env/key is missing, use a default", where the default is
   unsafe (empty secret, permissive CORS, TLS verification off, admin role).
5. **Hallucinated API.** A method, option or import that does not exist in the installed version
   of the library. Check against the lockfile version or the installed package.
6. **Mocked-away behaviour.** The new test mocks the function under test, or asserts on the mock
   instead of on the outcome.
7. **Happy-path-only fixtures.** Tests use data production never has (hand-built objects
   instead of the real factory, single-threaded execution, round sizes, ASCII-only text, one
   tenant).
8. **Copy without the guard.** A new route or handler copied from a sibling but missing the
   authorization, validation, transaction or rate-limit call the sibling has.
9. **Contract drift.** A helper changed to "simplify" it (raise → return `None`, sync → async,
   list → generator) without updating callers outside the diff.
10. **Scope creep.** Unrelated edits to config, CI, dependencies, lockfiles or formatting across
    many files, which hide the real change and its risks.
11. **Duplicated logic.** A new helper that re-implements an existing one with slightly different
    behaviour (rounding, validation, escaping), so the two now disagree.
12. **Stubs left behind.** `TODO`, `pass`, `return true`, `not implemented`, hard-coded test values
    or sample credentials in production paths.
