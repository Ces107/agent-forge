# EARS: writing acceptance criteria that can be traced and tested

EARS (Easy Approach to Requirements Syntax, Mavin et al., Rolls-Royce, 2009) is a small set of
sentence patterns that make a requirement unambiguous enough to test. AWS Kiro uses it for
spec-driven development.

## The five patterns

| Pattern | Template | Example |
|---|---|---|
| Ubiquitous | The `<system>` shall `<response>`. | The ledger shall store amounts as integer minor units. |
| Event-driven | When `<trigger>`, the `<system>` shall `<response>`. | When a request replays a seen idempotency key, the API shall return the original result. |
| State-driven | While `<state>`, the `<system>` shall `<response>`. | While two transfers touch the same account, the ledger shall serialise their writes. |
| Unwanted behaviour | If `<condition>`, then the `<system>` shall `<response>`. | If a transfer would overdraw an account, then the ledger shall reject it with 422. |
| Optional feature | Where `<feature>`, the `<system>` shall `<response>`. | Where multi-currency is enabled, the ledger shall reject cross-currency postings. |

Patterns combine: *While `<state>`, when `<trigger>`, the `<system>` shall `<response>`.*

## Rules for a good AC

1. **One behaviour per AC.** "and" in the response usually means two ACs.
2. **Observable from outside.** State what a caller or an operator can see, not how the code does it.
3. **Testable.** If you cannot name the test that would fail when the AC is violated, rewrite it.
4. **Stable id.** `AC-7` means the same thing forever. Retired requirements keep their number and
   are marked `(retired)`; new ones take the next free number.
5. **No weasel words.** Replace "fast", "robust", "user-friendly" with a number or a condition.

## Smells

- "The system shall handle errors gracefully." Which errors? What does the caller receive?
- "The system shall be secure." Split into concrete, testable ACs (authz on every route, no
  secrets in logs, rate limit on login).
- An AC that only a code reviewer can verify. It belongs in a design decision, not the spec.
