"""Unit + happy-path tests for the ledger (single-threaded).

Covers:
- Account creation
- A transfer writes exactly one debit + one matching credit
- Balance fold is correct after transfers
- Replay with the same idempotency_key returns the same result and creates no new postings
- Overdraft below floor is rejected with InsufficientFunds
- AccountNotFound raised for unknown accounts
- IdempotencyConflict raised when key reused with different payload
- Reconciliation: global debits == credits, per-account balance == fold
"""

import contextlib
import sqlite3

import pytest

from ledger.errors import AccountNotFound, IdempotencyConflict, InsufficientFunds
from ledger.service import TransferResult, transfer
from ledger.store import (
    account_balance,
    connect,
    create_account,
    init_schema,
    reconciliation,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_conn() -> sqlite3.Connection:
    """In-memory SQLite connection for test isolation."""
    conn = connect(":memory:")
    init_schema(conn)
    yield conn
    conn.close()


def _fund(conn: sqlite3.Connection, account_id: str, amount: int) -> None:
    """Helper: create account and give it a starting balance by transferring from a bank."""
    bank_id = f"__bank__{account_id}"
    create_account(conn, bank_id, balance_floor=-(10 ** 12))
    create_account(conn, account_id)
    transfer(
        conn,
        idempotency_key=f"seed-{account_id}",
        from_account=bank_id,
        to_account=account_id,
        amount=amount,
    )


# ---------------------------------------------------------------------------
# Account creation
# ---------------------------------------------------------------------------


def test_create_account_persists(db_conn: sqlite3.Connection) -> None:
    create_account(db_conn, "alice")
    row = db_conn.execute("SELECT id, balance_floor FROM accounts WHERE id='alice'").fetchone()
    assert row is not None
    assert row["id"] == "alice"
    assert row["balance_floor"] == 0


def test_balance_of_empty_account_is_zero(db_conn: sqlite3.Connection) -> None:
    create_account(db_conn, "alice")
    assert account_balance(db_conn, "alice") == 0


# ---------------------------------------------------------------------------
# Transfer: double-entry correctness
# ---------------------------------------------------------------------------


def test_transfer_writes_one_debit_and_one_credit(db_conn: sqlite3.Connection) -> None:
    _fund(db_conn, "alice", 1000)
    create_account(db_conn, "bob")

    result = transfer(
        db_conn,
        idempotency_key="txn-001",
        from_account="alice",
        to_account="bob",
        amount=300,
    )

    postings = db_conn.execute(
        "SELECT direction, amount FROM postings WHERE transfer_id = ?",
        (result.transfer_id,),
    ).fetchall()

    assert len(postings) == 2

    directions = {p["direction"]: p["amount"] for p in postings}
    assert directions["debit"] == 300
    assert directions["credit"] == 300


def test_transfer_updates_balances_correctly(db_conn: sqlite3.Connection) -> None:
    _fund(db_conn, "alice", 1000)
    create_account(db_conn, "bob")

    transfer(
        db_conn,
        idempotency_key="txn-001",
        from_account="alice",
        to_account="bob",
        amount=400,
    )

    assert account_balance(db_conn, "alice") == 600
    assert account_balance(db_conn, "bob") == 400


def test_transfer_result_fields(db_conn: sqlite3.Connection) -> None:
    _fund(db_conn, "alice", 500)
    create_account(db_conn, "bob")

    result = transfer(
        db_conn,
        idempotency_key="txn-result-check",
        from_account="alice",
        to_account="bob",
        amount=100,
    )

    assert isinstance(result, TransferResult)
    assert result.idempotency_key == "txn-result-check"
    assert result.from_account == "alice"
    assert result.to_account == "bob"
    assert result.amount == 100
    assert result.replayed is False


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_replay_returns_same_result(db_conn: sqlite3.Connection) -> None:
    _fund(db_conn, "alice", 1000)
    create_account(db_conn, "bob")

    first = transfer(
        db_conn,
        idempotency_key="idem-001",
        from_account="alice",
        to_account="bob",
        amount=200,
    )

    second = transfer(
        db_conn,
        idempotency_key="idem-001",
        from_account="alice",
        to_account="bob",
        amount=200,
    )

    assert second.transfer_id == first.transfer_id
    assert second.replayed is True


def test_replay_creates_no_new_postings(db_conn: sqlite3.Connection) -> None:
    _fund(db_conn, "alice", 1000)
    create_account(db_conn, "bob")

    first = transfer(
        db_conn,
        idempotency_key="idem-002",
        from_account="alice",
        to_account="bob",
        amount=200,
    )

    posting_count_before = db_conn.execute(
        "SELECT COUNT(*) as cnt FROM postings WHERE transfer_id=?",
        (first.transfer_id,),
    ).fetchone()["cnt"]

    # Replay
    transfer(
        db_conn,
        idempotency_key="idem-002",
        from_account="alice",
        to_account="bob",
        amount=200,
    )

    posting_count_after = db_conn.execute(
        "SELECT COUNT(*) as cnt FROM postings WHERE transfer_id=?",
        (first.transfer_id,),
    ).fetchone()["cnt"]

    assert posting_count_before == posting_count_after == 2


def test_replay_does_not_change_balance(db_conn: sqlite3.Connection) -> None:
    _fund(db_conn, "alice", 1000)
    create_account(db_conn, "bob")

    transfer(
        db_conn,
        idempotency_key="idem-003",
        from_account="alice",
        to_account="bob",
        amount=100,
    )

    balance_alice_after_first = account_balance(db_conn, "alice")
    balance_bob_after_first = account_balance(db_conn, "bob")

    # Replay three times
    for _ in range(3):
        transfer(
            db_conn,
            idempotency_key="idem-003",
            from_account="alice",
            to_account="bob",
            amount=100,
        )

    assert account_balance(db_conn, "alice") == balance_alice_after_first
    assert account_balance(db_conn, "bob") == balance_bob_after_first


def test_idempotency_conflict_on_different_payload(db_conn: sqlite3.Connection) -> None:
    _fund(db_conn, "alice", 1000)
    create_account(db_conn, "bob")
    create_account(db_conn, "carol")

    transfer(
        db_conn,
        idempotency_key="conflict-key",
        from_account="alice",
        to_account="bob",
        amount=50,
    )

    with pytest.raises(IdempotencyConflict):
        transfer(
            db_conn,
            idempotency_key="conflict-key",
            from_account="alice",
            to_account="carol",  # different destination
            amount=50,
        )


# ---------------------------------------------------------------------------
# Overdraft / floor
# ---------------------------------------------------------------------------


def test_overdraft_raises_insufficient_funds(db_conn: sqlite3.Connection) -> None:
    _fund(db_conn, "alice", 100)
    create_account(db_conn, "bob")

    with pytest.raises(InsufficientFunds):
        transfer(
            db_conn,
            idempotency_key="overdraft-001",
            from_account="alice",
            to_account="bob",
            amount=101,
        )


def test_overdraft_writes_nothing(db_conn: sqlite3.Connection) -> None:
    _fund(db_conn, "alice", 100)
    create_account(db_conn, "bob")

    balance_before = account_balance(db_conn, "alice")
    total_postings_before = (
        db_conn.execute("SELECT COUNT(*) as cnt FROM postings").fetchone()["cnt"]
    )

    with contextlib.suppress(InsufficientFunds):
        transfer(
            db_conn,
            idempotency_key="overdraft-002",
            from_account="alice",
            to_account="bob",
            amount=999,
        )

    assert account_balance(db_conn, "alice") == balance_before
    total_postings_after = (
        db_conn.execute("SELECT COUNT(*) as cnt FROM postings").fetchone()["cnt"]
    )
    assert total_postings_after == total_postings_before


def test_custom_floor_allows_negative_balance(db_conn: sqlite3.Connection) -> None:
    """An account with floor=-500 can go negative up to -500."""
    create_account(db_conn, "overdraft_account", balance_floor=-500)
    create_account(db_conn, "bank", balance_floor=-(10 ** 12))

    # Start at 0; draw down to -300 (within floor)
    transfer(
        db_conn,
        idempotency_key="floor-001",
        from_account="overdraft_account",
        to_account="bank",
        amount=300,
    )
    assert account_balance(db_conn, "overdraft_account") == -300


def test_custom_floor_rejects_breach(db_conn: sqlite3.Connection) -> None:
    """An account with floor=-500 rejects a transfer that would go to -501."""
    create_account(db_conn, "overdraft_account", balance_floor=-500)
    create_account(db_conn, "bank", balance_floor=-(10 ** 12))

    # Draw down to -300 first
    transfer(
        db_conn,
        idempotency_key="floor-002",
        from_account="overdraft_account",
        to_account="bank",
        amount=300,
    )

    # Try to go from -300 to -501 (breach)
    with pytest.raises(InsufficientFunds):
        transfer(
            db_conn,
            idempotency_key="floor-003",
            from_account="overdraft_account",
            to_account="bank",
            amount=201,
        )


def test_exact_floor_boundary_allowed(db_conn: sqlite3.Connection) -> None:
    """Transfer that lands exactly on the floor is accepted."""
    _fund(db_conn, "alice", 100)
    create_account(db_conn, "bob")

    result = transfer(
        db_conn,
        idempotency_key="floor-exact",
        from_account="alice",
        to_account="bob",
        amount=100,
    )
    assert result.replayed is False
    assert account_balance(db_conn, "alice") == 0


# ---------------------------------------------------------------------------
# AccountNotFound
# ---------------------------------------------------------------------------


def test_transfer_from_nonexistent_account_raises(db_conn: sqlite3.Connection) -> None:
    create_account(db_conn, "bob")

    with pytest.raises(AccountNotFound):
        transfer(
            db_conn,
            idempotency_key="notfound-001",
            from_account="nobody",
            to_account="bob",
            amount=100,
        )


def test_transfer_to_nonexistent_account_raises(db_conn: sqlite3.Connection) -> None:
    _fund(db_conn, "alice", 500)

    with pytest.raises(AccountNotFound):
        transfer(
            db_conn,
            idempotency_key="notfound-002",
            from_account="alice",
            to_account="nobody",
            amount=100,
        )


def test_notfound_writes_nothing(db_conn: sqlite3.Connection) -> None:
    _fund(db_conn, "alice", 500)
    total_before = db_conn.execute("SELECT COUNT(*) as cnt FROM postings").fetchone()["cnt"]
    idem_before = db_conn.execute("SELECT COUNT(*) as cnt FROM idempotency_keys").fetchone()["cnt"]

    with contextlib.suppress(AccountNotFound):
        transfer(
            db_conn,
            idempotency_key="notfound-003",
            from_account="alice",
            to_account="nobody",
            amount=100,
        )

    assert db_conn.execute("SELECT COUNT(*) as cnt FROM postings").fetchone()["cnt"] == total_before
    assert (
        db_conn.execute("SELECT COUNT(*) as cnt FROM idempotency_keys").fetchone()["cnt"]
        == idem_before
    )


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------


def test_reconciliation_balanced_after_transfers(db_conn: sqlite3.Connection) -> None:
    _fund(db_conn, "alice", 1000)
    _fund(db_conn, "bob", 500)
    create_account(db_conn, "carol")

    transfer(
        db_conn,
        idempotency_key="rec-001",
        from_account="alice",
        to_account="carol",
        amount=200,
    )
    transfer(
        db_conn,
        idempotency_key="rec-002",
        from_account="bob",
        to_account="carol",
        amount=100,
    )

    rec = reconciliation(db_conn)
    assert rec["balanced"] is True
    assert rec["total_debits"] == rec["total_credits"]


def test_reconciliation_per_account_balance_matches_fold(db_conn: sqlite3.Connection) -> None:
    _fund(db_conn, "alice", 600)
    create_account(db_conn, "bob")

    transfer(
        db_conn,
        idempotency_key="rec-fold-001",
        from_account="alice",
        to_account="bob",
        amount=150,
    )

    rec = reconciliation(db_conn)

    for acct_id, bal in rec["accounts"].items():
        assert bal == account_balance(db_conn, acct_id)
