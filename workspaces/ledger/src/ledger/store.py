"""Pure stdlib SQLite access layer.

Responsibilities:
- Connection factory with ADR-003 pragmas.
- Schema initialisation (CREATE TABLE IF NOT EXISTS).
- Low-level posting and idempotency writes (called inside a service transaction).
- Balance fold and reconciliation fold (pure reads).

This module has NO FastAPI imports and NO service-level logic.
"""

import sqlite3
from enum import StrEnum
from typing import Any

# ---------------------------------------------------------------------------
# Constants / enums
# ---------------------------------------------------------------------------


class Direction(StrEnum):
    DEBIT = "debit"
    CREDIT = "credit"


# ---------------------------------------------------------------------------
# Connection factory
# ---------------------------------------------------------------------------


def connect(db_path: str) -> sqlite3.Connection:
    """Open a SQLite connection with ADR-003 pragmas applied.

    isolation_level=None disables the sqlite3 driver's implicit transaction
    management so the service layer owns BEGIN/COMMIT boundaries explicitly.
    """
    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    _apply_pragmas(conn)
    return conn


def _apply_pragmas(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA busy_timeout=5000;")
    conn.execute("PRAGMA synchronous=NORMAL;")


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """
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

CREATE INDEX IF NOT EXISTS idx_postings_account  ON postings(account_id, direction);
CREATE INDEX IF NOT EXISTS idx_postings_transfer ON postings(transfer_id);

CREATE TABLE IF NOT EXISTS idempotency_keys (
  idempotency_key TEXT    PRIMARY KEY,
  request_hash    TEXT    NOT NULL,
  transfer_id     TEXT    NOT NULL,
  response_json   TEXT    NOT NULL,
  status_code     INTEGER NOT NULL,
  created_at      TEXT    NOT NULL
);
"""


def init_schema(conn: sqlite3.Connection) -> None:
    """Create all tables and indexes (idempotent — safe to call on every startup)."""
    conn.executescript(_SCHEMA_SQL)


# ---------------------------------------------------------------------------
# Account helpers
# ---------------------------------------------------------------------------


def create_account(
    conn: sqlite3.Connection,
    account_id: str,
    balance_floor: int = 0,
    created_at: str = "",
) -> None:
    """Insert a new account row.  created_at defaults to current UTC ISO-8601."""
    if not created_at:
        import datetime

        created_at = datetime.datetime.now(datetime.UTC).isoformat()
    conn.execute(
        "INSERT INTO accounts (id, balance_floor, created_at) VALUES (?, ?, ?)",
        (account_id, balance_floor, created_at),
    )


def get_account(conn: sqlite3.Connection, account_id: str) -> sqlite3.Row | None:
    """Return the accounts row or None if absent."""
    row: sqlite3.Row | None = conn.execute(
        "SELECT id, balance_floor, created_at FROM accounts WHERE id = ?",
        (account_id,),
    ).fetchone()
    return row


# ---------------------------------------------------------------------------
# Balance fold
# ---------------------------------------------------------------------------


def account_balance(conn: sqlite3.Connection, account_id: str) -> int:
    """Return balance as a fold over postings: SUM(credits) - SUM(debits).

    Does NOT verify the account exists — call get_account first if needed.
    """
    row = conn.execute(
        """
        SELECT
          COALESCE(SUM(CASE WHEN direction='credit' THEN amount ELSE 0 END), 0)
          - COALESCE(SUM(CASE WHEN direction='debit'  THEN amount ELSE 0 END), 0)
          AS balance
        FROM postings
        WHERE account_id = ?
        """,
        (account_id,),
    ).fetchone()
    return int(row["balance"])


# ---------------------------------------------------------------------------
# Reconciliation fold
# ---------------------------------------------------------------------------


def reconciliation(conn: sqlite3.Connection) -> dict[str, Any]:
    """Return the global reconciliation object.

    Shape::

        {
            "total_debits":  int,
            "total_credits": int,
            "balanced":      bool,
            "accounts":      {account_id: balance_int, ...},
        }
    """
    totals_row = conn.execute(
        """
        SELECT
          COALESCE(SUM(CASE WHEN direction='debit'  THEN amount ELSE 0 END), 0) AS total_debits,
          COALESCE(SUM(CASE WHEN direction='credit' THEN amount ELSE 0 END), 0) AS total_credits
        FROM postings
        """
    ).fetchone()

    total_debits = int(totals_row["total_debits"])
    total_credits = int(totals_row["total_credits"])

    account_rows = conn.execute("SELECT id FROM accounts").fetchall()
    accounts: dict[str, int] = {
        row["id"]: account_balance(conn, row["id"]) for row in account_rows
    }

    return {
        "total_debits": total_debits,
        "total_credits": total_credits,
        "balanced": total_debits == total_credits,
        "accounts": accounts,
    }


# ---------------------------------------------------------------------------
# Low-level write helpers (called INSIDE the service's BEGIN IMMEDIATE txn)
# ---------------------------------------------------------------------------


def insert_posting(
    conn: sqlite3.Connection,
    *,
    transfer_id: str,
    account_id: str,
    direction: Direction,
    amount: int,
    created_at: str,
) -> None:
    """Append a single posting row.  postings is append-only; no UPDATE/DELETE."""
    conn.execute(
        """
        INSERT INTO postings (transfer_id, account_id, direction, amount, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (transfer_id, account_id, direction.value, amount, created_at),
    )


def insert_idempotency_key(
    conn: sqlite3.Connection,
    *,
    idempotency_key: str,
    request_hash: str,
    transfer_id: str,
    response_json: str,
    status_code: int,
    created_at: str,
) -> None:
    """Insert the idempotency record.

    Raises sqlite3.IntegrityError (PK violation) if the key already exists —
    the service layer catches this to detect duplicate submissions.
    """
    conn.execute(
        """
        INSERT INTO idempotency_keys
          (idempotency_key, request_hash, transfer_id, response_json, status_code, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            idempotency_key,
            request_hash,
            transfer_id,
            response_json,
            status_code,
            created_at,
        ),
    )


def get_idempotency_record(
    conn: sqlite3.Connection, idempotency_key: str
) -> sqlite3.Row | None:
    """Return the stored idempotency row or None."""
    row: sqlite3.Row | None = conn.execute(
        "SELECT * FROM idempotency_keys WHERE idempotency_key = ?",
        (idempotency_key,),
    ).fetchone()
    return row
