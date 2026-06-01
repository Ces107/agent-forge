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

## Acceptance criteria (EARS, machine-traceable)

These are the falsifiable requirements. Each carries a stable `AC-N` id. `forge/work/tasks.md`
maps every `AC-N` to an implementation task and a real test; `forge.traceability` enforces that
mapping in CI (no requirement may be silently dropped). EARS = Easy Approach to Requirements
Syntax (Mavris/Rolls-Royce), the notation AWS Kiro adopts for spec-driven development.

- **AC-1** — When a transfer is accepted, the system shall write exactly one debit and one
  matching credit of equal magnitude (double-entry).
- **AC-2** — When a request replays an already-seen `idempotency_key`, the system shall return
  the original result and shall write no additional postings.
- **AC-3** — While duplicate submissions of one `idempotency_key` arrive concurrently, the system
  shall produce at most one posting pair.
- **AC-4** — While two transfers touch the same account concurrently, the system shall not lose an
  update (serialised read-modify-write).
- **AC-5** — The system shall never leave a transfer in a partial state: every transfer id shall
  have exactly two postings and the books shall close globally after every operation (atomicity /
  crash-safety).
- **AC-6** — The system shall conserve money: no code path shall create or destroy value
  (`sum(debits) == sum(credits)` always).
- **AC-7** — When a transfer would drive an account below its configured floor, the system shall
  reject it (no overdraft).
- **AC-8** — The system shall expose a reconciliation proof where global debits equal credits and
  each account balance equals the fold of its postings.
- **AC-9** — While write-lock contention exceeds `busy_timeout` under a retry storm, the system
  shall return a typed domain result (HTTP 503) and shall not leak an unhandled
  `OperationalError`. (This is the defect the AdversarialReviewer caught — see `REVIEW-001`.)
