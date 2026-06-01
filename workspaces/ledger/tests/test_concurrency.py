"""Adversarial concurrency tests for the ledger service.

Attack surface:
  1. Concurrent duplicate idempotency_key — retry storm with the same key from N threads.
  2. SQLITE_BUSY / OperationalError leaking as unhandled 500 when lock times out.
  3. Lost update on a shared account under interleaved concurrent transfers.
  4. Money conservation under concurrent load.

Each test uses a real temp-file DB (NOT :memory:) with one connection per thread,
matching production semantics (ADR-003 connection-per-request).

FINDING: Bug is in service.py — OperationalError from BEGIN IMMEDIATE timeout
(busy_timeout exhausted) is NOT handled and leaks to the caller as a raw
sqlite3.OperationalError instead of blocking/retrying or returning the idempotent
result.  The regression test (test_busy_timeout_does_not_leak_operational_error)
asserts the CORRECT behaviour and therefore FAILS against the present code.
"""

import sqlite3
import tempfile
import threading
import time
from pathlib import Path

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
# Shared helpers
# ---------------------------------------------------------------------------


def _make_db() -> str:
    """Return a path to a freshly-initialised temp-file SQLite database."""
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    f.close()
    conn = connect(f.name)
    init_schema(conn)
    conn.close()
    return f.name


def _fund(db_path: str, account_id: str, amount: int) -> None:
    """Seed an account with a starting balance via a bank transfer."""
    conn = connect(db_path)
    try:
        bank_id = f"__bank__{account_id}"
        create_account(conn, bank_id, balance_floor=-(10**12))
        create_account(conn, account_id)
        transfer(
            conn,
            idempotency_key=f"seed-{account_id}",
            from_account=bank_id,
            to_account=account_id,
            amount=amount,
        )
    finally:
        conn.close()


def _transfer_in_thread(
    db_path: str,
    idempotency_key: str,
    from_account: str,
    to_account: str,
    amount: int,
    results: list,
    errors: list,
    index: int,
) -> None:
    """Thread target: open its own connection, run transfer, collect result or error."""
    conn = connect(db_path)
    try:
        result = transfer(
            conn,
            idempotency_key=idempotency_key,
            from_account=from_account,
            to_account=to_account,
            amount=amount,
        )
        results[index] = result
    except (InsufficientFunds, IdempotencyConflict, AccountNotFound) as exc:
        errors[index] = exc
    except Exception as exc:  # noqa: BLE001 — capture unexpected errors for assertion
        errors[index] = exc
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Test 1 — Concurrent duplicate idempotency_key (retry storm)
#
# N threads fire the SAME key simultaneously.  The invariant is:
#   - Exactly one posting pair is written (the winner).
#   - Every thread that doesn't error returns either the winning result
#     (replayed=False) or the idempotent replay (replayed=True).
#   - No thread returns an unexpected exception (OperationalError, etc.).
# ---------------------------------------------------------------------------


def test_concurrent_same_idempotency_key_creates_exactly_one_posting_pair() -> None:
    """N threads, same key — exactly one debit+credit pair, no money duplication."""
    db_path = _make_db()
    N = 10

    conn_setup = connect(db_path)
    try:
        create_account(conn_setup, "alice")
        create_account(conn_setup, "bob")
        bank = "__bank__alice"
        create_account(conn_setup, bank, balance_floor=-(10**12))
        transfer(
            conn_setup,
            idempotency_key="seed-alice-storm",
            from_account=bank,
            to_account="alice",
            amount=10_000,
        )
    finally:
        conn_setup.close()

    results: list = [None] * N
    errors: list = [None] * N
    barrier = threading.Barrier(N)

    def _thread(i: int) -> None:
        barrier.wait()  # All threads start simultaneously
        _transfer_in_thread(
            db_path,
            idempotency_key="storm-key-001",
            from_account="alice",
            to_account="bob",
            amount=100,
            results=results,
            errors=errors,
            index=i,
        )

    threads = [threading.Thread(target=_thread, args=(i,)) for i in range(N)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    # No thread should have raised an unexpected error
    unexpected = [e for e in errors if e is not None and not isinstance(e, (InsufficientFunds, IdempotencyConflict, AccountNotFound))]
    assert unexpected == [], f"Unexpected errors from threads: {unexpected}"

    # Count posting pairs written
    check_conn = connect(db_path)
    try:
        count = check_conn.execute(
            "SELECT COUNT(DISTINCT transfer_id) FROM postings"
            " WHERE transfer_id NOT LIKE 'seed%'"
        ).fetchone()[0]
        # There must be exactly 1 unique transfer_id for our storm key
        storm_count = check_conn.execute(
            """
            SELECT COUNT(DISTINCT p.transfer_id) FROM postings p
            JOIN idempotency_keys ik ON ik.transfer_id = p.transfer_id
            WHERE ik.idempotency_key = 'storm-key-001'
            """
        ).fetchone()[0]
        assert storm_count == 1, (
            f"Expected exactly 1 transfer for storm-key-001, got {storm_count}"
        )

        # Books must close
        rec = reconciliation(check_conn)
        assert rec["balanced"], f"Books are not balanced after storm: {rec}"
    finally:
        check_conn.close()


# ---------------------------------------------------------------------------
# Test 2 — SQLITE_BUSY / OperationalError leak
#
# We force a deterministic lock-contention scenario by opening a second
# connection that holds BEGIN IMMEDIATE and then launching the service
# transfer() with a deliberately low busy_timeout (monkeypatched to near-zero)
# so the race is guaranteed rather than probabilistic.
#
# CORRECT behaviour: transfer() should propagate a domain-legible error or
# at minimum NOT leak sqlite3.OperationalError (the API layer cannot map it).
#
# ACTUAL behaviour (bug): sqlite3.OperationalError escapes transfer(), surfaces
# as an unhandled 500 in the API layer.
#
# This test asserts the CORRECT behaviour and therefore FAILS against present code.
# ---------------------------------------------------------------------------


def test_busy_timeout_does_not_leak_operational_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Regression test for the OperationalError leak.

    When busy_timeout is exhausted while waiting for BEGIN IMMEDIATE, the service
    MUST NOT raise sqlite3.OperationalError to the caller.  It should either:
      (a) retry and eventually succeed/replay, OR
      (b) raise a domain LedgerError that the API can map to a retryable HTTP status.

    Currently the raw OperationalError escapes — this test documents the correct
    contract and FAILS against the present code.
    """
    db_path = _make_db()

    # Setup accounts
    setup_conn = connect(db_path)
    try:
        create_account(setup_conn, "alice2")
        create_account(setup_conn, "bob2")
        bank = "__bank__alice2"
        create_account(setup_conn, bank, balance_floor=-(10**12))
        transfer(
            setup_conn,
            idempotency_key="seed-alice2",
            from_account=bank,
            to_account="alice2",
            amount=5_000,
        )
    finally:
        setup_conn.close()

    # Hold the write lock from a second connection
    blocker_conn = sqlite3.connect(db_path, isolation_level=None)
    blocker_conn.execute("PRAGMA journal_mode=WAL;")
    blocker_conn.execute("BEGIN IMMEDIATE")

    # Patch busy_timeout to near-zero on the service connection so we deterministically
    # hit the timeout rather than waiting 5 seconds.
    original_apply_pragmas = __import__("ledger.store", fromlist=["_apply_pragmas"])._apply_pragmas

    def patched_apply_pragmas(conn: sqlite3.Connection) -> None:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.execute("PRAGMA busy_timeout=1;")   # 1 ms — guaranteed timeout
        conn.execute("PRAGMA synchronous=NORMAL;")

    monkeypatch.setattr("ledger.store._apply_pragmas", patched_apply_pragmas)

    victim_conn = connect(db_path)
    try:
        # This MUST NOT raise sqlite3.OperationalError.
        # The correct behaviour is to raise a LedgerError subclass or succeed.
        try:
            transfer(
                victim_conn,
                idempotency_key="busy-key-001",
                from_account="alice2",
                to_account="bob2",
                amount=50,
            )
        except sqlite3.OperationalError as exc:
            # BUG: OperationalError leaked — this is what we're documenting.
            pytest.fail(
                f"sqlite3.OperationalError leaked from transfer() instead of a domain error: {exc}"
            )
        except Exception:
            # Any other exception (LedgerError subclass, etc.) is acceptable —
            # the important thing is that OperationalError did NOT leak.
            pass
    finally:
        victim_conn.close()
        blocker_conn.execute("ROLLBACK")
        blocker_conn.close()


# ---------------------------------------------------------------------------
# Test 3 — Lost update on a shared account under concurrent transfers
#
# Two threads each try to transfer from alice to different recipients at the
# same time.  alice has exactly enough for both.  After both complete (or one
# fails with InsufficientFunds) the books must still close and alice's balance
# must not go below zero.
# ---------------------------------------------------------------------------


def test_no_lost_update_concurrent_drains() -> None:
    """Concurrent transfers from the same account don't create a double-spend."""
    db_path = _make_db()
    N = 8

    setup_conn = connect(db_path)
    try:
        create_account(setup_conn, "shared_source")
        bank = "__bank__shared_source"
        create_account(setup_conn, bank, balance_floor=-(10**12))
        transfer(
            setup_conn,
            idempotency_key="seed-shared-source",
            from_account=bank,
            to_account="shared_source",
            amount=1_000,  # exactly 10 * 100
        )
        for i in range(N):
            create_account(setup_conn, f"recipient_{i}")
    finally:
        setup_conn.close()

    results: list = [None] * N
    errors: list = [None] * N
    barrier = threading.Barrier(N)

    def _thread(i: int) -> None:
        barrier.wait()
        _transfer_in_thread(
            db_path,
            idempotency_key=f"drain-{i}",
            from_account="shared_source",
            to_account=f"recipient_{i}",
            amount=200,  # 8 * 200 = 1600 > 1000, so some must fail
            results=results,
            errors=errors,
            index=i,
        )

    threads = [threading.Thread(target=_thread, args=(i,)) for i in range(N)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    # No unexpected errors (OperationalError etc.)
    unexpected = [
        e for e in errors
        if e is not None and not isinstance(e, (InsufficientFunds, IdempotencyConflict, AccountNotFound))
    ]
    assert unexpected == [], f"Unexpected errors: {unexpected}"

    # Books must close
    check_conn = connect(db_path)
    try:
        rec = reconciliation(check_conn)
        assert rec["balanced"], f"Books unbalanced: {rec}"

        # shared_source must not go below 0
        bal = account_balance(check_conn, "shared_source")
        assert bal >= 0, f"shared_source balance went below floor: {bal}"
    finally:
        check_conn.close()


# ---------------------------------------------------------------------------
# Test 4 — Money conservation under a storm of concurrent transfers
#
# Run M threads each doing independent transfers.  Sum of all balances must
# equal the originally-seeded amount (no money created or destroyed).
# ---------------------------------------------------------------------------


def test_money_conservation_under_concurrent_load() -> None:
    """No money is created or destroyed under concurrent transfer storm."""
    db_path = _make_db()
    N_ACCOUNTS = 4
    SEED = 10_000
    N_THREADS = 20

    setup_conn = connect(db_path)
    try:
        for i in range(N_ACCOUNTS):
            acc = f"acc_{i}"
            bank = f"__bank__{acc}"
            create_account(setup_conn, bank, balance_floor=-(10**12))
            create_account(setup_conn, acc)
            transfer(
                setup_conn,
                idempotency_key=f"seed-{acc}",
                from_account=bank,
                to_account=acc,
                amount=SEED,
            )
    finally:
        setup_conn.close()

    results: list = [None] * N_THREADS
    errors: list = [None] * N_THREADS
    barrier = threading.Barrier(N_THREADS)

    def _thread(i: int) -> None:
        barrier.wait()
        src = f"acc_{i % N_ACCOUNTS}"
        dst = f"acc_{(i + 1) % N_ACCOUNTS}"
        _transfer_in_thread(
            db_path,
            idempotency_key=f"load-{i}",
            from_account=src,
            to_account=dst,
            amount=500,
            results=results,
            errors=errors,
            index=i,
        )

    threads = [threading.Thread(target=_thread, args=(i,)) for i in range(N_THREADS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    unexpected = [
        e for e in errors
        if e is not None and not isinstance(e, (InsufficientFunds, IdempotencyConflict, AccountNotFound))
    ]
    assert unexpected == [], f"Unexpected errors under concurrent load: {unexpected}"

    check_conn = connect(db_path)
    try:
        rec = reconciliation(check_conn)
        assert rec["balanced"], f"Books unbalanced after concurrent load: {rec}"

        # Total balance across real (non-bank) accounts must be exactly N_ACCOUNTS * SEED
        total_balance = sum(
            bal for acc_id, bal in rec["accounts"].items()
            if not acc_id.startswith("__bank__")
        )
        # Bank accounts absorb the double-entry; the real accounts' net sum
        # equals SEED * N_ACCOUNTS (money that entered the system via seeding).
        # This verifies conservation across the real accounts only.
        # (Bank accounts may go negative because they are the seeding source.)
        # What we assert: real accounts + bank accounts = 0 (global conservation).
        total_all = sum(rec["accounts"].values())
        assert total_all == 0, (
            f"Global money conservation violated: net sum = {total_all} (expected 0)"
        )
    finally:
        check_conn.close()


# ---------------------------------------------------------------------------
# Test 5 — Concurrent storm: all threads get a valid result (no OperationalError)
#
# A softer version of test 2: use production busy_timeout=5000ms and run
# 20 threads with the same key. All must get either a TransferResult or a
# LedgerError subclass — never a raw sqlite3 error.
# ---------------------------------------------------------------------------


def test_retry_storm_all_threads_return_domain_result() -> None:
    """20 threads, same key, 5 s busy_timeout — every thread gets a domain result."""
    db_path = _make_db()
    N = 20

    setup_conn = connect(db_path)
    try:
        bank = "__bank__storm_src"
        create_account(setup_conn, bank, balance_floor=-(10**12))
        create_account(setup_conn, "storm_src")
        create_account(setup_conn, "storm_dst")
        transfer(
            setup_conn,
            idempotency_key="seed-storm-src",
            from_account=bank,
            to_account="storm_src",
            amount=100_000,
        )
    finally:
        setup_conn.close()

    results: list = [None] * N
    errors: list = [None] * N
    barrier = threading.Barrier(N)

    def _thread(i: int) -> None:
        barrier.wait()
        _transfer_in_thread(
            db_path,
            idempotency_key="retry-storm-key",
            from_account="storm_src",
            to_account="storm_dst",
            amount=1_000,
            results=results,
            errors=errors,
            index=i,
        )

    threads = [threading.Thread(target=_thread, args=(i,)) for i in range(N)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    # Every thread must have gotten a domain result (TransferResult) or a domain error.
    # A raw sqlite3.OperationalError is a bug.
    for i in range(N):
        r, e = results[i], errors[i]
        assert (r is not None) or (e is not None), f"Thread {i} produced neither result nor error"
        if e is not None:
            assert isinstance(e, (InsufficientFunds, IdempotencyConflict, AccountNotFound)), (
                f"Thread {i} raised a non-domain exception: {type(e).__name__}: {e}"
            )

    # Exactly one posting pair
    check_conn = connect(db_path)
    try:
        storm_count = check_conn.execute(
            """
            SELECT COUNT(DISTINCT p.transfer_id) FROM postings p
            JOIN idempotency_keys ik ON ik.transfer_id = p.transfer_id
            WHERE ik.idempotency_key = 'retry-storm-key'
            """
        ).fetchone()[0]
        assert storm_count == 1, f"Expected 1 transfer for retry-storm-key, got {storm_count}"

        rec = reconciliation(check_conn)
        assert rec["balanced"], f"Books unbalanced after storm: {rec}"
    finally:
        check_conn.close()
