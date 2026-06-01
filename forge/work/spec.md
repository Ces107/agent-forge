# Acceptance spec — idempotent double-entry payments ledger

Derived from `forge/work/mandate.md`. This is the product/acceptance definition (the WHAT).
The Architect decides the HOW (ADRs); the Implementer writes all application code.

## Scope (deliberately narrow)
ONE bounded context: exactly-once money movement under concurrent retry. No auth, no FX /
multi-currency, no admin UI. Small enough that a reviewer holds the whole correctness model in
their head.

## API surface
- `POST /transfers` — body `{idempotency_key, from_account, to_account, amount}`.
  Returns the transfer result. A replay with the same `idempotency_key` returns the **same**
  result and performs **no** additional posting.
- `GET /accounts/{id}/balance` — current balance (a fold over postings, not a mutable column).
- `GET /reconciliation` — proof object: global `sum(debits) == sum(credits)` and per-account
  balance == fold of postings.

## Invariants (the Verifier asserts these; property tests must exercise them)
1. **Double-entry**: every accepted transfer writes exactly one debit and one matching credit of
   equal magnitude. The books close globally at all times.
2. **Idempotency**: a given `idempotency_key` produces at most one set of postings, even under
   concurrent duplicate submission. Replays are safe and return the original result.
3. **Exactly-once across restart**: a crash between debit and credit must not lose or duplicate
   money — the two postings are atomic.
4. **No overdraft** below a configurable per-account floor (default 0).
5. **Conservation**: no code path creates or destroys money.

## Acceptance gates
- Hypothesis stateful (`RuleBasedStateMachine`) model of the ledger — passes.
- Concurrency test: N threads firing the same `idempotency_key` → exactly one posting set.
- Concurrency test: interleaved transfers on one account → no lost update.
- Crash-recovery test: simulated failure between postings → books still close.
- Reconciliation invariant test → global debits == credits.
- Coverage ≥ 80 %; `ruff`, `mypy --strict`, `bandit` clean; no secrets.
- Reviewer can `docker compose up` + run the suite from a clean clone in < 5 minutes.
