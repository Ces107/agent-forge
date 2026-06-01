# ADR-001 — Double-entry schema

## Context
The ledger must guarantee that the books close globally at all times: total debits
equal total credits, and any account balance is reproducible from primitive records.
Mutable balance columns are the classic source of drift (a write that updates the
balance but not the journal, or vice versa). The core must be pure stdlib
(`sqlite3`) so correctness is testable without FastAPI.

## Decision
Two tables only.

- `accounts(id TEXT PK, balance_floor INTEGER NOT NULL DEFAULT 0, created_at TEXT)`.
  The floor is a per-account configurable overdraft limit (default 0). It holds NO
  balance column — balance is never stored.
- `postings` is **append-only / immutable**: rows are inserted, never updated or
  deleted. Each posting carries `transfer_id`, `account_id`, `direction`
  (`'debit'|'credit'`), `amount INTEGER` (minor units, strictly > 0), `created_at`.

Every accepted transfer writes **exactly two postings**: one `debit` on
`from_account` and one `credit` on `to_account`, of **equal magnitude**. Sign
convention: a debit decreases the source balance, a credit increases the
destination balance. Balance is a fold:

```
balance(a) = SUM(credit.amount) - SUM(debit.amount)  WHERE account_id = a
```

Amounts are integer minor units (cents). No floats anywhere — float arithmetic
breaks conservation. A `CHECK(amount > 0)` forbids zero/negative legs; direction
encodes the sign.

## Consequences
- The journal is the single source of truth; balance and reconciliation are pure
  reads (`SUM` folds), trivially auditable and impossible to desync.
- Append-only postings give a complete immutable history for free (no separate
  audit log).
- Balance reads are O(postings-per-account). Acceptable at this scope; if it ever
  matters, a materialised view or covering index `(account_id, direction)` is an
  additive optimisation that does not change the contract.
- Integer minor units push currency-formatting concerns entirely to the edge.

## Rejected alternatives
- **Mutable `balance` column on `accounts`.** Rejected: two writes that can diverge;
  the exact drift the spec forbids. A balance column would have to be reconciled
  against the fold anyway, so it earns nothing but a failure mode.
- **Single signed `amount` per posting (one row per transfer).** Rejected: loses the
  explicit debit/credit pairing the Verifier asserts and makes
  `SUM(debits)==SUM(credits)` un-checkable as a structural invariant.
- **Decimal/float money.** Rejected: non-associative rounding violates conservation.
