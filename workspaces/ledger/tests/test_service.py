"""Targeted service.py branch tests — covers lines not reached by test_basic.py.

Missing branches:
  - Line 126: non-lock OperationalError re-raised from _begin_immediate
  - Line 158: InvalidAmount raised when amount <= 0
  - Line 192: LookupError raised when IntegrityError fires but no idempotency row found
              (defensive "should never happen" path)
"""

import sqlite3

import pytest

from ledger.errors import InvalidAmount
from ledger.service import transfer
from ledger.store import connect, create_account, init_schema

# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_conn() -> sqlite3.Connection:
    conn = connect(":memory:")
    init_schema(conn)
    yield conn
    conn.close()


# ---------------------------------------------------------------------------
# Line 158 — InvalidAmount guard (amount <= 0)
# ---------------------------------------------------------------------------


def test_transfer_zero_amount_raises_invalid_amount(db_conn: sqlite3.Connection) -> None:
    """amount=0 must raise InvalidAmount before touching the DB."""
    create_account(db_conn, "alice")
    create_account(db_conn, "bob")

    with pytest.raises(InvalidAmount, match="positive"):
        transfer(
            db_conn,
            idempotency_key="inv-zero",
            from_account="alice",
            to_account="bob",
            amount=0,
        )


def test_transfer_negative_amount_raises_invalid_amount(db_conn: sqlite3.Connection) -> None:
    """amount=-1 must raise InvalidAmount before touching the DB."""
    create_account(db_conn, "alice")
    create_account(db_conn, "bob")

    with pytest.raises(InvalidAmount, match="positive"):
        transfer(
            db_conn,
            idempotency_key="inv-neg",
            from_account="alice",
            to_account="bob",
            amount=-1,
        )


def test_transfer_invalid_amount_writes_nothing(db_conn: sqlite3.Connection) -> None:
    """InvalidAmount must not leave any postings or idempotency records."""
    create_account(db_conn, "alice")
    create_account(db_conn, "bob")

    with pytest.raises(InvalidAmount):
        transfer(
            db_conn,
            idempotency_key="inv-clean",
            from_account="alice",
            to_account="bob",
            amount=0,
        )

    assert db_conn.execute("SELECT COUNT(*) FROM postings").fetchone()[0] == 0
    assert db_conn.execute("SELECT COUNT(*) FROM idempotency_keys").fetchone()[0] == 0


# ---------------------------------------------------------------------------
# Line 126 — non-lock OperationalError re-raised from _begin_immediate
# ---------------------------------------------------------------------------


def test_begin_immediate_reraises_non_lock_operational_error() -> None:
    """A non-lock OperationalError from conn.execute('BEGIN IMMEDIATE') is re-raised as-is.

    We trigger this by calling transfer() on a connection that is already in a
    transaction (BEGIN IMMEDIATE on an isolation_level=None connection raises
    'cannot start a transaction within a transaction' which is not a lock error).
    """
    import ledger.service as svc

    # Access the private function directly to test the exact branch.
    db_conn = connect(":memory:")
    init_schema(db_conn)

    # Put the connection into an open transaction so BEGIN IMMEDIATE raises
    # "cannot start a transaction within a transaction" — a non-lock error.
    db_conn.execute("BEGIN")

    with pytest.raises(sqlite3.OperationalError) as exc_info:
        svc._begin_immediate(db_conn)  # noqa: SLF001

    # Must NOT have been wrapped as ServiceUnavailable
    err_msg = str(exc_info.value).lower()
    assert "cannot start" in err_msg or "transaction" in err_msg
    db_conn.execute("ROLLBACK")
    db_conn.close()


# ---------------------------------------------------------------------------
# Line 192 — LookupError when IntegrityError fires but stored row is missing
# (defensive path: "should never happen" in production but must be covered)
# ---------------------------------------------------------------------------


def test_lookup_error_when_idempotency_row_missing_after_integrity_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    When an IntegrityError fires on INSERT idempotency_key but get_idempotency_record
    returns None (e.g. a race cleared the row), LookupError must be raised.
    """
    import ledger.service as svc
    import ledger.store as store_mod

    db_conn = connect(":memory:")
    init_schema(db_conn)
    create_account(db_conn, "alice")
    create_account(db_conn, "bob")

    def fake_insert(*args: object, **kwargs: object) -> None:
        raise sqlite3.IntegrityError("UNIQUE constraint failed")

    def fake_get(conn: sqlite3.Connection, key: str) -> None:
        return None  # simulate missing row

    monkeypatch.setattr(store_mod, "insert_idempotency_key", fake_insert)
    monkeypatch.setattr(svc, "insert_idempotency_key", fake_insert)
    monkeypatch.setattr(store_mod, "get_idempotency_record", fake_get)
    monkeypatch.setattr(svc, "get_idempotency_record", fake_get)

    with pytest.raises(LookupError, match="Idempotency record missing"):
        transfer(
            db_conn,
            idempotency_key="phantom-key",
            from_account="alice",
            to_account="bob",
            amount=50,
        )

    db_conn.close()
