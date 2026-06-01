"""Crash-recovery test (AC-5): a crash between debit and credit must not lose or duplicate money.

The spec (forge/work/spec.md, AC-5) promises a "simulated failure between postings -> books still
close" test. This is it. It is the guarantee a payments ledger lives or dies on: a process that dies
mid-transfer must leave the books closed, with no half-posted transfer.

How the crash is simulated (genuinely, not as a caught exception): the service wraps its work in
``try: ... except Exception: ROLLBACK; raise``. We make the *credit* posting raise a
``BaseException`` subclass, which is NOT an ``Exception``, so the clean-up rollback never runs and
the transaction is abandoned exactly as on a SIGKILL / power loss (idempotency key + debit written
but uncommitted). We then drop the connection (the process "dies") and open a fresh one: SQLite
must discard the uncommitted transaction, the books still close, and the transfer leaves no trace.
"""

import tempfile

import pytest

from ledger import service
from ledger.service import transfer
from ledger.store import (
    account_balance,
    connect,
    create_account,
    init_schema,
    insert_posting,
    reconciliation,
)


class _SimulatedCrash(BaseException):
    """Process-death signal. BaseException (not Exception) escapes the service rollback handler."""


def _fresh_db() -> str:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        path = tmp.name
    conn = connect(path)
    init_schema(conn)
    conn.close()
    return path


def _fund(path: str, account_id: str, amount: int) -> None:
    conn = connect(path)
    try:
        bank = f"__bank__{account_id}"
        create_account(conn, bank, balance_floor=-(10**12))
        create_account(conn, account_id)
        transfer(
            conn,
            idempotency_key=f"seed-{account_id}",
            from_account=bank,
            to_account=account_id,
            amount=amount,
        )
    finally:
        conn.close()


def _posting_count(path: str) -> int:
    conn = connect(path)
    try:
        return int(conn.execute("SELECT COUNT(*) AS n FROM postings").fetchone()["n"])
    finally:
        conn.close()


def _balance_of(path: str, account_id: str) -> int:
    conn = connect(path)
    try:
        return account_balance(conn, account_id)
    finally:
        conn.close()


def _crash_on_credit() -> object:
    """Return an insert_posting replacement that crashes on the 2nd call (the credit)."""
    calls = {"n": 0}

    def crashing(*args: object, **kwargs: object) -> None:
        calls["n"] += 1
        if calls["n"] == 2:  # 1 = debit (written), 2 = credit -> crash before COMMIT
            raise _SimulatedCrash("process killed between debit and credit")
        return insert_posting(*args, **kwargs)  # type: ignore[arg-type]

    return crashing


def test_crash_between_postings_leaves_books_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    path = _fresh_db()
    _fund(path, "alice", 100)
    conn0 = connect(path)
    try:
        create_account(conn0, "bob")
    finally:
        conn0.close()

    before = _posting_count(path)
    assert _balance_of(path, "alice") == 100
    assert _balance_of(path, "bob") == 0

    monkeypatch.setattr(service, "insert_posting", _crash_on_credit())
    conn = connect(path)
    with pytest.raises(_SimulatedCrash):
        transfer(
            conn,
            idempotency_key="crash-key",
            from_account="alice",
            to_account="bob",
            amount=30,
        )
    # The process "dies": no commit, no rollback ran (BaseException escaped the handler).
    conn.close()

    # Recovery: a fresh connection must see a consistent, closed ledger.
    recovered = connect(path)
    try:
        recon = reconciliation(recovered)
        assert recon["balanced"], "books did not close after a crash mid-transfer"
        assert recon["total_debits"] == recon["total_credits"]
        # The crashed transfer left NO trace: no extra postings, balances unchanged.
        assert _posting_count(path) == before
        assert account_balance(recovered, "alice") == 100
        assert account_balance(recovered, "bob") == 0
        # The idempotency key rolled back with the transaction, so a retry can still claim it.
        leftover = recovered.execute(
            "SELECT COUNT(*) AS n FROM idempotency_keys WHERE idempotency_key = ?",
            ("crash-key",),
        ).fetchone()["n"]
        assert leftover == 0
    finally:
        recovered.close()


def test_retry_after_crash_succeeds_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    """After a crash rolled the transfer back, replaying the request must succeed exactly once."""
    path = _fresh_db()
    _fund(path, "alice", 100)
    conn0 = connect(path)
    try:
        create_account(conn0, "bob")
    finally:
        conn0.close()

    monkeypatch.setattr(service, "insert_posting", _crash_on_credit())
    conn = connect(path)
    with pytest.raises(_SimulatedCrash):
        transfer(conn, idempotency_key="k1", from_account="alice", to_account="bob", amount=30)
    conn.close()

    # Un-patch: the "restarted process" works normally and the retry goes through once.
    monkeypatch.setattr(service, "insert_posting", insert_posting)
    conn2 = connect(path)
    try:
        result = transfer(
            conn2, idempotency_key="k1", from_account="alice", to_account="bob", amount=30
        )
        assert result.replayed is False
        recon = reconciliation(conn2)
        assert recon["balanced"]
        assert account_balance(conn2, "alice") == 70
        assert account_balance(conn2, "bob") == 30
    finally:
        conn2.close()
