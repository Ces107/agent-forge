# Code review: Make POST /charges idempotent

**Verdict: BLOCK** · 3 findings (3 proven) · Scope: main..feature, 3 files, +28 −5 ·
Tests: `python -m pytest` passed (2)

The change stores an idempotency key on each charge and returns the stored charge when the same
key is seen again. That works for a retry that arrives after the first request has finished, but
not for the case the PR is about: a client that times out and retries while the first request is
still waiting on the processor gets charged twice, because the lookup and the insert are separate
steps and the index on the key is not unique. Separately, `connect()` now crashes on any database
created before this change, so deploying it stops every charge. Fix both before merging.

## Findings

### 1. [CRITICAL · Proven] `payments/service.py:16-28`: a retry that overlaps the first request still double-charges
**What happens:** the client times out while `gateway.capture()` is in flight and retries with the
same key → the retry's `SELECT` (`:16`) finds no row, because the first request only inserts it
after the capture returns (`:22-28`) → both requests call `gateway.capture()` and the customer is
charged twice. This is exactly the timeout-and-retry scenario in the PR description. The index
created in `payments/db.py:14` is not `UNIQUE`, so nothing at the database level stops the second
row either. The same ordering also means that if the `INSERT` fails after a successful capture
(for example `database is locked` after the 5 s timeout), the money is taken with no record, and
the client's retry captures again.

**Evidence:**
```
$ python -m pytest tests/test_review_proof_idempotency.py::test_review_proof_concurrent_retry_captures_once
E       AssertionError: [('cus_1', 500), ('cus_1', 500)]
E       assert 2 == 1
1 failed in 3.94s
```

**Fix:** claim the key atomically before the side effect, with a unique index (see finding 2 for
the column migration), and release the claim if the capture fails:
```python
charge_id = uuid.uuid4().hex
try:
    with conn:
        conn.execute(
            "INSERT INTO charges (charge_id, customer_id, amount_cents, txn_id, idempotency_key)"
            " VALUES (?, ?, ?, 'pending', ?)",
            (charge_id, customer_id, amount_cents, idempotency_key),
        )
except sqlite3.IntegrityError:
    row = conn.execute(
        "SELECT charge_id FROM charges WHERE customer_id = ? AND idempotency_key = ?",
        (customer_id, idempotency_key),
    ).fetchone()
    return row[0]
try:
    txn_id = gateway.capture(customer_id, amount_cents)
except Exception:
    with conn:
        conn.execute("DELETE FROM charges WHERE charge_id = ?", (charge_id,))
    raise
with conn:
    conn.execute("UPDATE charges SET txn_id = ? WHERE charge_id = ?", (txn_id, charge_id))
return charge_id
```
I ran this fix (together with the `db.py` change in finding 2) against the proof test below: one
capture, both callers get the same `charge_id`. If the processor accepts an idempotency key,
pass it through `capture()` as well.

<details><summary>Proof test (add it to the suite)</summary>

```python
import threading
import time

from payments.db import connect
from payments.gateway import Gateway
from payments.service import charge


def test_review_proof_concurrent_retry_captures_once(tmp_path):
    path = str(tmp_path / "p.db")
    connect(path).close()
    gateway = Gateway()
    real_capture = gateway.capture

    def slow_capture(customer_id, amount_cents):
        time.sleep(0.2)  # the processor call that timed out on the client
        return real_capture(customer_id, amount_cents)

    gateway.capture = slow_capture
    barrier = threading.Barrier(2)
    results = []

    def worker():
        conn = connect(path)  # one connection per request, like the real service
        barrier.wait()
        results.append(charge(conn, gateway, "cus_1", 500, "key-1"))

    threads = [threading.Thread(target=worker) for _ in range(2)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(gateway.captured) == 1, gateway.captured
    assert results[0] == results[1]
```
</details>

### 2. [CRITICAL · Proven] `payments/db.py:6-14`: `connect()` crashes on every existing database
**What happens:** production already has a `charges` table (the PR mentions last month's double
charges). `CREATE TABLE IF NOT EXISTS` is a no-op on that table, so the new `idempotency_key`
column is never added, and the next statement, `CREATE INDEX ... (idempotency_key)`, raises →
every call to `connect()` fails and no charge can be taken after deploy. The test suite does not
see it because every test starts from an empty `tmp_path` database.

**Evidence:**
```
$ python -m pytest tests/test_review_proof_idempotency.py::test_review_proof_existing_database_still_opens
>       conn.execute("CREATE INDEX IF NOT EXISTS charges_idem ON charges (idempotency_key)")
E       sqlite3.OperationalError: no such column: idempotency_key
payments\db.py:14: OperationalError
```

**Fix:** add the column when it is missing, and make the index unique (it also backs finding 1;
rows from before the change keep `NULL` keys, which SQLite allows in a unique index):
```python
cols = {row[1] for row in conn.execute("PRAGMA table_info(charges)")}
if "idempotency_key" not in cols:
    conn.execute("ALTER TABLE charges ADD COLUMN idempotency_key TEXT")
conn.execute(
    "CREATE UNIQUE INDEX IF NOT EXISTS charges_idem ON charges (customer_id, idempotency_key)"
)
```
Verified: with this change an old-schema database containing existing rows opens and charges
normally.

<details><summary>Proof test (add it to the suite)</summary>

```python
import sqlite3

from payments.db import connect
from payments.gateway import Gateway
from payments.service import charge


def test_review_proof_existing_database_still_opens(tmp_path):
    path = str(tmp_path / "prod.db")
    old = sqlite3.connect(path)  # schema as created by main's connect()
    old.execute(
        "CREATE TABLE charges (charge_id TEXT PRIMARY KEY, customer_id TEXT NOT NULL,"
        " amount_cents INTEGER NOT NULL, txn_id TEXT NOT NULL)"
    )
    old.commit()
    old.close()
    conn = connect(path)
    charge(conn, Gateway(), "cus_1", 500, "key-1")
```
</details>

### 3. [MEDIUM · Proven] `payments/service.py:16-20`: the key is global, not per customer, and the request is not compared
**What happens:** the lookup matches on `idempotency_key` alone. If two customers' clients send
the same key (keys derived from an order number, a per-device counter, a reused constant), the
second customer is never charged and receives the first customer's `charge_id`. A same-key retry
with a different amount is also silently answered with the original charge instead of being
rejected.

**Evidence:**
```
$ python -m pytest tests/test_review_proof_idempotency.py::test_review_proof_key_reused_by_other_customer
E       AssertionError: assert '7a1f6c7733eb443195a28dfb27b9bc23' != '7a1f6c7733eb443195a28dfb27b9bc23'
```

**Fix:** scope the key by customer (the `(customer_id, idempotency_key)` unique index and the
lookup in finding 1 already do this), and reject a reuse with different parameters:
```python
row = conn.execute(
    "SELECT charge_id, amount_cents FROM charges WHERE customer_id = ? AND idempotency_key = ?",
    (customer_id, idempotency_key),
).fetchone()
if row and row[1] != amount_cents:
    raise ValueError("idempotency key reused with a different amount")
```

<details><summary>Proof test (add it to the suite)</summary>

```python
def test_review_proof_key_reused_by_other_customer(tmp_path):
    conn = connect(str(tmp_path / "p.db"))
    gateway = Gateway()
    a = charge(conn, gateway, "cus_1", 500, "order-42")
    b = charge(conn, gateway, "cus_2", 9900, "order-42")
    assert b != a
    assert ("cus_2", 9900) in gateway.captured
```
</details>

## Questions for the author
- Where does the key come from on the HTTP side, and is it mandatory? `charge()` now requires it,
  but the route that reads it from the request is not in this repository.

## Checked and fine
- Callers: `charge()` has no callers outside the tests, so the new required parameter breaks
  nothing in this repository.
- Amount validation still runs before the lookup, so an invalid retry is still rejected.
- All SQL uses bound parameters; no injection through the key.
- A sequential retry (second call after the first has committed) returns the same `charge_id`
  and captures once, as the new test shows.
- Existing suite: 2 passed.
