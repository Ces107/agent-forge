# ADR-002 — Idempotency and stored response

## Context
`POST /transfers` is fired under retry storms: the same logical transfer may arrive
many times (client retry, proxy retry, at-least-once delivery). The contract: a
given `idempotency_key` produces **at most one** set of postings, and every replay
returns the **same** result body — not an error, not a second posting pair.

## Decision
A dedicated table records the outcome of each key:

```
idempotency_keys(
  idempotency_key TEXT PRIMARY KEY,   -- UNIQUE by construction
  request_hash    TEXT NOT NULL,      -- sha256 of canonical (from,to,amount)
  transfer_id     TEXT NOT NULL,
  response_json   TEXT NOT NULL,      -- the exact stored response body
  status_code     INTEGER NOT NULL,
  created_at      TEXT NOT NULL
)
```

**Key scope:** global / service-wide, not per-account. A key is a client-chosen
unique token for one logical operation; scoping it to an account would let the same
key mean two different transfers. The `PRIMARY KEY` is the uniqueness mechanism.

**Stored-response contract:** on first success we store the *full response body* the
client received (`response_json` + `status_code`) keyed by `idempotency_key`. A
replay does a lookup and returns that stored body verbatim — the response is
deterministic across replays and across process restarts because it is persisted,
not recomputed.

**Request-hash guard:** we also store `request_hash`. If a key is replayed with a
*different* `(from, to, amount)` payload, that is a client bug (key reuse for a
different operation). We return **409 Conflict** and write nothing. Same key + same
payload → return the stored response. This makes idempotency safe and detects
misuse instead of silently mis-routing money.

The write of the two postings (ADR-001) and the insert into `idempotency_keys`
happen in the **same transaction** (see ADR-003). The `PRIMARY KEY` insert is what
serialises concurrent duplicates: the second writer's insert raises
`IntegrityError`, the transaction rolls back its postings, and that path then reads
and returns the stored response.

## Consequences
- Exactly-once is enforced by the database, not by application-level locks or
  best-effort dedup caches — survives restart and crash.
- Replays are cheap single-row reads.
- A first request still in-flight (postings written, not yet committed) blocks the
  duplicate at the `BEGIN IMMEDIATE` write lock (ADR-003), so there is no
  read-before-commit race.
- `request_hash` adds one sha256 per request — negligible, and turns a dangerous
  silent failure into a loud 409.

## Rejected alternatives
- **`idempotency_key` UNIQUE only, recompute response on replay.** Rejected:
  recomputation can drift (clock, ordering) and a replay arriving mid-flight has
  nothing to recompute from. Storing the response is the only restart-safe answer.
- **In-memory dedup set / LRU cache.** Rejected: lost on restart, not shared across
  workers — violates exactly-once-across-restart.
- **Per-account key scope.** Rejected: the same token could denote two transfers;
  global scope matches client intent of "one operation, one key".
- **Silently returning the stored response on payload mismatch.** Rejected: hides a
  client bug and could mis-report a different transfer's result as this one's.
