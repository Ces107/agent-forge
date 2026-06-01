# ADR-003 — Concurrency and crash recovery

## Context
Under retry storms, N requests with the same `idempotency_key`, and interleaved
transfers on the same account, hit SQLite concurrently. Two failure modes must be
impossible: (a) lost updates / a second posting set for a duplicate key, (b) money
lost or duplicated by a crash between the debit and the credit. SQLite is a
single-writer engine; we lean on that property deliberately.

## Decision
**Connection-per-request**, WAL mode, with one `BEGIN IMMEDIATE` transaction per
transfer that performs the idempotency check **and** both postings **and** the
stored-response insert atomically.

Connection pragmas (set on every connection):
```
PRAGMA journal_mode=WAL;      -- readers don't block the single writer
PRAGMA foreign_keys=ON;
PRAGMA busy_timeout=5000;     -- wait, don't fail, on the write lock
PRAGMA synchronous=NORMAL;    -- (FULL acceptable; durability vs throughput)
```

`isolation_level=None` (autocommit off via explicit SQL) so the driver does not
inject its own implicit transactions — we own the BEGIN/COMMIT boundary.

The service transaction is exactly:
```
BEGIN IMMEDIATE;                       -- acquires the write lock NOW, not lazily
  INSERT INTO idempotency_keys(...);   -- PK; raises IntegrityError on duplicate
  -- read balance fold of from_account; enforce floor (overdraft check)
  INSERT INTO postings(debit  on from_account);
  INSERT INTO postings(credit on to_account);
COMMIT;
```
`BEGIN IMMEDIATE` takes the database write lock up front, so two concurrent
transfers are **serialised** — the second waits (up to `busy_timeout`) for the
first to COMMIT or ROLLBACK. This eliminates lost updates: the balance fold and the
overdraft check are read inside the same exclusive write transaction that appends
the postings, so no interleaving can read a stale balance.

**Duplicate idempotency key:** the loser's `INSERT INTO idempotency_keys` violates
the PK → `IntegrityError` → the whole transaction (including its postings) ROLLs
BACK. The handler catches it, opens a fresh read, and returns the stored response
(ADR-002). Net effect: exactly one posting set per key, no matter how many threads
race.

**Crash recovery:** the idempotency row + both postings are in ONE transaction.
SQLite gives all-or-nothing commit (WAL frame is atomic). A crash before COMMIT
leaves zero of the three rows — the transfer simply never happened and a retry
(same key) succeeds cleanly. A crash after COMMIT leaves all three — a retry finds
the stored response and returns it. There is **no intermediate state** where one
posting exists without its pair or without its idempotency record. Money is never
half-moved.

## Consequences
- Writes are serialised (single-writer). At this scope that is the correctness
  feature, not a bottleneck; reads (balances, reconciliation) run concurrently under
  WAL.
- `busy_timeout` converts lock contention into latency, not errors, under storms.
- The whole correctness argument reduces to one DB transaction boundary — small
  enough to hold in your head, exactly as the spec asks.

## Rejected alternatives
- **`BEGIN DEFERRED` (default).** Rejected: the write lock is acquired lazily at
  first write, leaving a window where two transactions both read a stale balance
  before either writes → `SQLITE_BUSY`/lost-update risk. `IMMEDIATE` closes it.
- **Application-level mutex / advisory lock.** Rejected: doesn't survive multi-process
  or restart; the DB write lock already provides the guarantee for free.
- **Shared long-lived connection across threads.** Rejected: SQLite connections are
  not safe to share across threads without care; connection-per-request is simpler
  and isolates transaction state.
- **Two separate transactions (postings, then idempotency record).** Rejected:
  reintroduces the half-moved-money crash window the spec forbids.

---

## Contract for the Implementer

**Module layout** (ledger core is pure stdlib; API is the only FastAPI-touching module):
- `ledger/errors.py` — exception types.
- `ledger/store.py` — SQLite access: schema init, connection factory (pragmas
  above), low-level posting/idempotency writes and balance/reconciliation folds.
- `ledger/service.py` — pure orchestration: the `BEGIN IMMEDIATE` transaction,
  idempotency + overdraft logic. No web imports.
- `ledger/api.py` — thin FastAPI layer: request models, calls `service`, maps
  exceptions to HTTP status codes.

**Public signatures:**
```python
# ledger/errors.py
class LedgerError(Exception): ...
class InsufficientFunds(LedgerError): ...          # -> HTTP 422
class IdempotencyConflict(LedgerError): ...         # key reused, different payload -> HTTP 409
class AccountNotFound(LedgerError): ...             # -> HTTP 404

# ledger/store.py
def connect(db_path: str) -> sqlite3.Connection: ...      # applies all pragmas
def init_schema(conn: sqlite3.Connection) -> None: ...    # CREATE TABLE IF NOT EXISTS (below)
def account_balance(conn: sqlite3.Connection, account_id: str) -> int: ...
def reconciliation(conn: sqlite3.Connection) -> dict: ...
    # -> {"total_debits": int, "total_credits": int, "balanced": bool,
    #     "accounts": {account_id: balance_int, ...}}

# ledger/service.py
@dataclass(frozen=True)
class TransferResult:
    transfer_id: str
    idempotency_key: str
    from_account: str
    to_account: str
    amount: int
    replayed: bool

def transfer(conn: sqlite3.Connection, *, idempotency_key: str,
             from_account: str, to_account: str, amount: int) -> TransferResult: ...
    # one BEGIN IMMEDIATE txn; raises InsufficientFunds / IdempotencyConflict / AccountNotFound

# ledger/api.py  (FastAPI endpoints)
POST /transfers              body {idempotency_key, from_account, to_account, amount} -> 200/201 TransferResult
GET  /accounts/{id}/balance  -> {"account_id": str, "balance": int}
GET  /reconciliation         -> reconciliation() object
```

**SQL schema (canonical):**
```sql
CREATE TABLE IF NOT EXISTS accounts (
  id            TEXT PRIMARY KEY,
  balance_floor INTEGER NOT NULL DEFAULT 0,
  created_at    TEXT    NOT NULL
);
CREATE TABLE IF NOT EXISTS postings (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  transfer_id TEXT    NOT NULL,
  account_id  TEXT    NOT NULL REFERENCES accounts(id),
  direction   TEXT    NOT NULL CHECK (direction IN ('debit','credit')),
  amount      INTEGER NOT NULL CHECK (amount > 0),
  created_at  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_postings_account ON postings(account_id, direction);
CREATE INDEX IF NOT EXISTS idx_postings_transfer ON postings(transfer_id);
CREATE TABLE IF NOT EXISTS idempotency_keys (
  idempotency_key TEXT    PRIMARY KEY,
  request_hash    TEXT    NOT NULL,
  transfer_id     TEXT    NOT NULL,
  response_json   TEXT    NOT NULL,
  status_code     INTEGER NOT NULL,
  created_at      TEXT    NOT NULL
);
```
postings is append-only: implementer MUST NOT emit UPDATE or DELETE against it.

**Invariants as testable predicates** (the Verifier asserts these):
1. Conservation / books close: `SUM(amount WHERE direction='debit') == SUM(amount WHERE direction='credit')` over all postings, at all times.
2. Balance is a fold: for every account `a`, `account_balance(conn,a) == SUM(credit.amount) - SUM(debit.amount)` for `a`. No mutable balance column exists.
3. Pairing: for every `transfer_id`, exactly two postings — one `debit` and one `credit` of equal `amount`.
4. Idempotency: for any `idempotency_key`, `COUNT(DISTINCT transfer_id) <= 1`; N concurrent identical submissions ⇒ exactly one posting pair; replay returns the stored response with `replayed=True`.
5. No overdraft: after any accepted transfer, `account_balance(conn,a) >= accounts.balance_floor` (default 0); a transfer that would breach the floor raises `InsufficientFunds` and writes nothing.
6. Atomicity / crash safety: the idempotency row and both postings commit together or not at all — no `transfer_id` ever has 1 posting.
