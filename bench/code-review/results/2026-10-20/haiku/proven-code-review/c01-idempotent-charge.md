# Code review: Make POST /charges idempotent

**Verdict: BLOCK** · 3 findings (3 proven) · Scope: main..feature, 3 files, 28 insertions(+), 5 deletions(-) · Tests: `python -m pytest` passed (2 existing tests)

The change stores the idempotency key on the charge and returns the original charge on a sequential
retry. Three things stop it from being mergeable: the existing database cannot be opened after the
deploy (the schema change never reaches existing tables), concurrent retries still capture twice (the
exact failure the PR describes), and the key is not scoped to the customer, so a reused key silently
returns another customer's charge.

## Findings

### 1. [CRITICAL · Proven] `payments/db.py:14`: existing databases fail to open after the change
**What happens:** a deployed database already has the `charges` table without `idempotency_key` →
`connect()` runs `CREATE TABLE IF NOT EXISTS`, which is a no-op for an existing table → the next
statement, `CREATE INDEX ... ON charges (idempotency_key)`, fails → every process that calls
`connect()` on the production database crashes on startup. This is an outage of the main path.

**Evidence:**
```
$ python -m pytest tests/test_review_proof_migr.py  (proof file, deleted afterwards)
payments\db.py:14: OperationalError
E       sqlite3.OperationalError: no such column: idempotency_key
1 failed in 2.06s
```
The proof creates a `charges` table with the schema on `main`, inserts one row, then calls `connect()`.

**Fix:** add the column to existing tables explicitly, before the index:
```python
conn.execute("CREATE TABLE IF NOT EXISTS charges (...)")  # unchanged, for new databases
cols = {r[1] for r in conn.execute("PRAGMA table_info(charges)")}
if "idempotency_key" not in cols:
    conn.execute("ALTER TABLE charges ADD COLUMN idempotency_key TEXT")
conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS charges_idem ON charges (customer_id, idempotency_key)")
```

<details><summary>Proof test (add it to the suite)</summary>

```python
import sqlite3
from payments.db import connect


def test_review_proof_existing_db_migrates(tmp_path):
    path = str(tmp_path / "old.db")
    old = sqlite3.connect(path)  # schema as shipped on main
    old.execute("CREATE TABLE charges (charge_id TEXT PRIMARY KEY, customer_id TEXT NOT NULL,"
                " amount_cents INTEGER NOT NULL, txn_id TEXT NOT NULL)")
    old.execute("INSERT INTO charges VALUES ('c1', 'cus_1', 500, 'txn-1')")
    old.commit()
    old.close()
    connect(path)
```
</details>

### 2. [CRITICAL · Proven] `payments/service.py:16-22`: concurrent retries both capture
**What happens:** two requests with the same key arrive together → both run the `SELECT` at line 16
before either inserts → both find no row → both call `gateway.capture` at line 22 → the customer is
charged twice, which is the incident the PR is meant to prevent. The `idempotency_key` index is not
`UNIQUE`, so the database does not stop the second insert either.

**Evidence:**
```
$ python -m pytest tests/test_review_proof_idem.py  (proof file, deleted afterwards)
>       assert len(gateway.captured) == 1
E       AssertionError: assert 2 == 1
E        +  where 2 = len([('cus_1', 500), ('cus_1', 500)])
FAILED tests/test_review_proof_idem.py::test_review_proof_concurrent_retries_capture_once
```
The proof runs two threads, each with its own SQLite connection to the same file, released together by
a `threading.Barrier(2)`, against a gateway whose `capture` sleeps 0.2 s to widen the window.

**Fix:** claim the key atomically before calling the gateway, using a unique constraint:
```python
# payments/service.py
def charge(conn, gateway, customer_id, amount_cents, idempotency_key):
    if amount_cents <= 0:
        raise ValueError("amount must be positive")
    charge_id = uuid.uuid4().hex
    try:
        conn.execute(
            "INSERT INTO charges (charge_id, customer_id, amount_cents, txn_id, idempotency_key)"
            " VALUES (?, ?, ?, NULL, ?)",
            (charge_id, customer_id, amount_cents, idempotency_key),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.rollback()
        row = conn.execute(
            "SELECT charge_id FROM charges WHERE customer_id = ? AND idempotency_key = ?",
            (customer_id, idempotency_key),
        ).fetchone()
        return row[0]
    txn_id = gateway.capture(customer_id, amount_cents)
    conn.execute("UPDATE charges SET txn_id = ? WHERE charge_id = ?", (txn_id, charge_id))
    conn.commit()
    return charge_id
```
This needs `txn_id` to be nullable (pending claim) and the unique index from finding 1. A claim left with
`txn_id IS NULL` after a crash must be reconciled, not returned as a success. See question 2.

<details><summary>Proof test (add it to the suite)</summary>

```python
import threading
import time

from payments.db import connect
from payments.gateway import Gateway
from payments.service import charge


class SlowGateway(Gateway):
    def capture(self, customer_id, amount_cents):
        time.sleep(0.2)
        return super().capture(customer_id, amount_cents)


def test_review_proof_concurrent_retries_capture_once(tmp_path):
    path = str(tmp_path / "p.db")
    connect(path).close()
    gateway = SlowGateway()
    barrier = threading.Barrier(2)

    def worker():
        conn = connect(path)
        barrier.wait()
        charge(conn, gateway, "cus_1", 500, "key-1")

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(gateway.captured) == 1
```
</details>

### 3. [HIGH · Proven] `payments/service.py:16-18`: key is not scoped to the customer
**What happens:** customer `cus_1` is charged with key `key-1` → customer `cus_2` sends a request with
the same key `key-1` → the lookup matches `cus_1`'s row and returns its `charge_id` → `cus_2` is
never captured, and the caller believes the charge succeeded.

**Evidence:**
```
$ python -m pytest tests/test_review_proof_idem.py  (proof file, deleted afterwards)
>       assert gateway.captured == [("cus_1", 500), ("cus_2", 900)]
E       AssertionError: assert [('cus_1', 500)] == [('cus_1', 50...'cus_2', 900)]
E        Right contains one more item: ('cus_2', 900)
```

**Fix:** include the customer in the lookup (and in the unique index from finding 1):
```python
existing = conn.execute(
    "SELECT charge_id FROM charges WHERE customer_id = ? AND idempotency_key = ?",
    (customer_id, idempotency_key),
).fetchone()
```

<details><summary>Proof test (add it to the suite)</summary>

```python
def test_review_proof_key_reused_across_customers(tmp_path):
    conn = connect(str(tmp_path / "p.db"))
    gateway = Gateway()
    first = charge(conn, gateway, "cus_1", 500, "key-1")
    second = charge(conn, gateway, "cus_2", 900, "key-1")
    assert gateway.captured == [("cus_1", 500), ("cus_2", 900)]
    assert second != first
```
</details>

## Questions for the author
- The PR says `POST /charges`, but no route exists in this repository, only `charge()`. Where is the
  endpoint, and does it pass a key scoped per client?
- If the gateway call succeeds and the process dies before the INSERT commits, the retry captures
  again. Is that accepted, or should the gateway be queried by the key before a second capture?

## Checked and fine
- Amount validation runs before any side effect (`service.py:14-15`).
- A sequential retry with the same key and customer returns the original `charge_id` and captures once
  (`tests/test_service.py:13-18`, passing).
- No other callers of `charge()` exist in the repository besides its tests, so the new required
  `idempotency_key` parameter breaks nothing else here (but see finding 1 for deployed databases).

## Nits
- A retry with the same key but a different amount returns the original charge without any mismatch
  check. Consider raising when `amount_cents` differs from the stored row.
