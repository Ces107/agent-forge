"""Hypothesis stateful property tests for the ledger (single-threaded).

Models random sequences of:
  - account creation (with varying balance floors)
  - transfers (random source, destination, amount)
  - replays (same idempotency_key re-submitted)

Invariants checked after every step:
  1. Global conservation: SUM(debit.amount) == SUM(credit.amount)
  2. Per-account balance == fold of postings
  3. No balance below floor for any account
  4. Every transfer_id has exactly two postings (one debit, one credit, equal amount)
  5. No idempotency_key maps to more than one transfer_id
"""

import sqlite3
from dataclasses import dataclass

import pytest
from hypothesis import settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule

from ledger.errors import InsufficientFunds
from ledger.service import transfer
from ledger.store import (
    account_balance,
    connect,
    create_account,
    init_schema,
    reconciliation,
)

# ---------------------------------------------------------------------------
# State model
# ---------------------------------------------------------------------------


@dataclass
class AccountModel:
    account_id: str
    balance_floor: int
    balance: int = 0  # shadow balance tracked by the model


# ---------------------------------------------------------------------------
# Machine
# ---------------------------------------------------------------------------


class LedgerMachine(RuleBasedStateMachine):
    """Hypothesis stateful machine: randomised account + transfer sequences."""

    def __init__(self) -> None:
        super().__init__()
        self._conn: sqlite3.Connection = connect(":memory:")
        init_schema(self._conn)
        # Model state
        self._accounts: dict[str, AccountModel] = {}
        self._idempotency_keys: dict[str, str] = {}  # key -> transfer_id
        self._key_payloads: dict[str, tuple[str, str, int]] = {}  # key -> (from,to,amt)
        self._next_key: int = 0

    def _fresh_key(self) -> str:
        k = f"key-{self._next_key}"
        self._next_key += 1
        return k

    # ------------------------------------------------------------------
    # Rules
    # ------------------------------------------------------------------

    @rule(
        account_id=st.text(
            alphabet=st.characters(whitelist_categories=("Ll", "Lu", "Nd"), min_codepoint=97),
            min_size=1,
            max_size=12,
        ),
        balance_floor=st.integers(min_value=-10_000, max_value=0),
    )
    def create_account_rule(self, account_id: str, balance_floor: int) -> None:
        """Create an account (skip if already exists)."""
        if account_id in self._accounts:
            return
        create_account(self._conn, account_id, balance_floor=balance_floor)
        self._accounts[account_id] = AccountModel(
            account_id=account_id,
            balance_floor=balance_floor,
            balance=0,
        )

    @rule(
        from_key=st.integers(min_value=0),
        to_key=st.integers(min_value=0),
        amount=st.integers(min_value=1, max_value=1_000),
    )
    def transfer_rule(self, from_key: int, to_key: int, amount: int) -> None:
        """Attempt a transfer between two random accounts."""
        if len(self._accounts) < 2:
            return

        acct_ids = list(self._accounts.keys())
        from_id = acct_ids[from_key % len(acct_ids)]
        to_id = acct_ids[to_key % len(acct_ids)]

        if from_id == to_id:
            return

        idem_key = self._fresh_key()
        from_model = self._accounts[from_id]
        to_model = self._accounts[to_id]

        would_be = from_model.balance - amount
        if would_be < from_model.balance_floor:
            # Expect InsufficientFunds — verify it is raised
            with pytest.raises(InsufficientFunds):
                transfer(
                    self._conn,
                    idempotency_key=idem_key,
                    from_account=from_id,
                    to_account=to_id,
                    amount=amount,
                )
            return

        result = transfer(
            self._conn,
            idempotency_key=idem_key,
            from_account=from_id,
            to_account=to_id,
            amount=amount,
        )

        assert result.replayed is False
        from_model.balance -= amount
        to_model.balance += amount
        self._idempotency_keys[idem_key] = result.transfer_id
        self._key_payloads[idem_key] = (from_id, to_id, amount)

    @rule(key_index=st.integers(min_value=0))
    def replay_transfer_rule(self, key_index: int) -> None:
        """Replay a previously seen transfer — must return same result, no new postings."""
        if not self._idempotency_keys:
            return

        keys = list(self._idempotency_keys.keys())
        idem_key = keys[key_index % len(keys)]
        from_id, to_id, amount = self._key_payloads[idem_key]
        expected_transfer_id = self._idempotency_keys[idem_key]

        result = transfer(
            self._conn,
            idempotency_key=idem_key,
            from_account=from_id,
            to_account=to_id,
            amount=amount,
        )

        assert result.replayed is True
        assert result.transfer_id == expected_transfer_id
        # Model balances must NOT change
        # (invariants will confirm the DB also unchanged)

    # ------------------------------------------------------------------
    # Invariants (checked after every step by Hypothesis)
    # ------------------------------------------------------------------

    @invariant()
    def books_close_globally(self) -> None:
        """SUM(debits) == SUM(credits) at all times."""
        rec = reconciliation(self._conn)
        assert rec["balanced"] is True, (
            f"Books not balanced: debits={rec['total_debits']} credits={rec['total_credits']}"
        )

    @invariant()
    def per_account_balance_equals_fold(self) -> None:
        """Every account balance in the model matches the DB fold."""
        for acct_id, model in self._accounts.items():
            db_bal = account_balance(self._conn, acct_id)
            assert db_bal == model.balance, (
                f"Balance mismatch for {acct_id!r}: model={model.balance} db={db_bal}"
            )

    @invariant()
    def no_balance_below_floor(self) -> None:
        """No account balance is below its configured floor."""
        for acct_id, model in self._accounts.items():
            db_bal = account_balance(self._conn, acct_id)
            assert db_bal >= model.balance_floor, (
                f"Account {acct_id!r} below floor: balance={db_bal} floor={model.balance_floor}"
            )

    @invariant()
    def transfer_ids_have_exactly_two_postings(self) -> None:
        """Every committed transfer_id has exactly one debit and one credit of equal amount."""
        rows = self._conn.execute(
            """
            SELECT transfer_id,
                   SUM(CASE WHEN direction='debit'  THEN 1 ELSE 0 END) AS n_debits,
                   SUM(CASE WHEN direction='credit' THEN 1 ELSE 0 END) AS n_credits,
                   SUM(CASE WHEN direction='debit'  THEN amount ELSE 0 END) AS debit_amt,
                   SUM(CASE WHEN direction='credit' THEN amount ELSE 0 END) AS credit_amt
            FROM postings
            GROUP BY transfer_id
            """
        ).fetchall()

        for row in rows:
            tid = row["transfer_id"]
            assert row["n_debits"] == 1, f"transfer {tid}: n_debits={row['n_debits']}"
            assert row["n_credits"] == 1, f"transfer {tid}: n_credits={row['n_credits']}"
            assert row["debit_amt"] == row["credit_amt"], (
                f"transfer {tid}: debit_amt={row['debit_amt']} credit_amt={row['credit_amt']}"
            )

    @invariant()
    def idempotency_key_maps_to_at_most_one_transfer(self) -> None:
        """No idempotency_key maps to more than one transfer_id."""
        rows = self._conn.execute(
            "SELECT idempotency_key, COUNT(DISTINCT transfer_id) AS n"
            " FROM idempotency_keys GROUP BY idempotency_key"
        ).fetchall()
        for row in rows:
            assert row["n"] <= 1, (
                f"Key {row['idempotency_key']!r} maps to {row['n']} transfer_ids"
            )

    def teardown(self) -> None:
        self._conn.close()


# ---------------------------------------------------------------------------
# Hypothesis settings + entry point
# ---------------------------------------------------------------------------

LedgerStatefulTest = LedgerMachine.TestCase
LedgerStatefulTest.settings = settings(
    max_examples=100,
    stateful_step_count=40,
    deadline=None,
)
