"""Pure orchestration layer — no FastAPI imports.

Implements the single BEGIN IMMEDIATE transaction described in ADR-003:

    BEGIN IMMEDIATE
      INSERT INTO idempotency_keys  -- PK; IntegrityError on duplicate
      balance fold of from_account  -- overdraft check inside write lock
      INSERT posting (debit)
      INSERT posting (credit)
    COMMIT

All three rows (idempotency + debit + credit) commit atomically or not at all.
"""

import contextlib
import datetime
import hashlib
import json
import sqlite3
import time
import uuid
from dataclasses import dataclass

from ledger.errors import (
    AccountNotFound,
    IdempotencyConflict,
    InsufficientFunds,
    InvalidAmount,
    ServiceUnavailable,
)
from ledger.store import (
    Direction,
    account_balance,
    get_account,
    get_idempotency_record,
    insert_idempotency_key,
    insert_posting,
)

# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TransferResult:
    transfer_id: str
    idempotency_key: str
    from_account: str
    to_account: str
    amount: int
    replayed: bool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _request_hash(from_account: str, to_account: str, amount: int) -> str:
    """Canonical SHA-256 of the transfer payload (ADR-002 request-hash guard)."""
    canonical = json.dumps(
        {"from_account": from_account, "to_account": to_account, "amount": amount},
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _utcnow() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


def _result_to_json(result: TransferResult) -> str:
    return json.dumps(
        {
            "transfer_id": result.transfer_id,
            "idempotency_key": result.idempotency_key,
            "from_account": result.from_account,
            "to_account": result.to_account,
            "amount": result.amount,
            "replayed": result.replayed,
        }
    )


def _result_from_json(raw: str, replayed: bool) -> TransferResult:
    data = json.loads(raw)
    return TransferResult(
        transfer_id=data["transfer_id"],
        idempotency_key=data["idempotency_key"],
        from_account=data["from_account"],
        to_account=data["to_account"],
        amount=data["amount"],
        replayed=replayed,
    )


# ---------------------------------------------------------------------------
# Write-lock acquisition helpers
# ---------------------------------------------------------------------------

_LOCK_BUSY_MSGS = frozenset({"database is locked", "database is busy"})
_LOCK_MAX_ATTEMPTS = 3
_LOCK_BASE_SLEEP_S = 0.05  # 50 ms, doubles each attempt


def _is_lock_error(exc: sqlite3.OperationalError) -> bool:
    msg = str(exc).lower()
    return any(busy in msg for busy in _LOCK_BUSY_MSGS)


def _begin_immediate(conn: sqlite3.Connection) -> None:
    """Acquire the SQLite write lock via BEGIN IMMEDIATE with bounded retry-backoff.

    Retries up to _LOCK_MAX_ATTEMPTS times on lock-busy OperationalError with
    exponential backoff (50 ms, 100 ms, ...).  Raises ServiceUnavailable if
    all attempts are exhausted.
    """
    sleep = _LOCK_BASE_SLEEP_S
    for attempt in range(1, _LOCK_MAX_ATTEMPTS + 1):
        try:
            conn.execute("BEGIN IMMEDIATE")
            return
        except sqlite3.OperationalError as exc:
            if not _is_lock_error(exc):
                raise
            if attempt == _LOCK_MAX_ATTEMPTS:
                raise ServiceUnavailable(
                    "Write lock unavailable after retries; please retry the request"
                ) from exc
            time.sleep(sleep)
            sleep *= 2


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def transfer(
    conn: sqlite3.Connection,
    *,
    idempotency_key: str,
    from_account: str,
    to_account: str,
    amount: int,
) -> TransferResult:
    """Execute a double-entry transfer under a single BEGIN IMMEDIATE transaction.

    Raises:
        InvalidAmount        — amount is not a positive integer.
        AccountNotFound      — from_account or to_account does not exist.
        InsufficientFunds    — transfer would push from_account below its floor.
        IdempotencyConflict  — key already used with a different (from, to, amount).
        ServiceUnavailable   — write lock could not be acquired within retry budget.
    """
    if amount <= 0:
        raise InvalidAmount(f"Transfer amount must be positive, got {amount!r}")

    req_hash = _request_hash(from_account, to_account, amount)
    transfer_id = str(uuid.uuid4())
    now = _utcnow()

    _begin_immediate(conn)
    try:
        # --- idempotency: try to claim the key atomically ---
        try:
            # Build the result now so we can store it; replayed=False for first write.
            first_result = TransferResult(
                transfer_id=transfer_id,
                idempotency_key=idempotency_key,
                from_account=from_account,
                to_account=to_account,
                amount=amount,
                replayed=False,
            )
            insert_idempotency_key(
                conn,
                idempotency_key=idempotency_key,
                request_hash=req_hash,
                transfer_id=transfer_id,
                response_json=_result_to_json(first_result),
                status_code=200,
                created_at=now,
            )
        except sqlite3.IntegrityError as exc:
            # Duplicate key — roll back this transaction and return stored response.
            conn.execute("ROLLBACK")
            stored = get_idempotency_record(conn, idempotency_key)
            if stored is None:
                # Should never happen: IntegrityError but no stored row.
                raise LookupError(
                    f"Idempotency record missing for key {idempotency_key!r}"
                ) from exc
            if stored["request_hash"] != req_hash:
                raise IdempotencyConflict(
                    f"Idempotency key {idempotency_key!r} reused with different payload"
                ) from None
            return _result_from_json(stored["response_json"], replayed=True)

        # --- account existence checks ---
        from_row = get_account(conn, from_account)
        if from_row is None:
            conn.execute("ROLLBACK")
            raise AccountNotFound(f"Account not found: {from_account!r}")

        to_row = get_account(conn, to_account)
        if to_row is None:
            conn.execute("ROLLBACK")
            raise AccountNotFound(f"Account not found: {to_account!r}")

        # --- overdraft check (balance fold inside the write lock) ---
        current_balance = account_balance(conn, from_account)
        floor = int(from_row["balance_floor"])
        if current_balance - amount < floor:
            conn.execute("ROLLBACK")
            raise InsufficientFunds(
                f"Transfer of {amount} from {from_account!r} would breach floor {floor} "
                f"(current balance: {current_balance})"
            )

        # --- write the two postings ---
        insert_posting(
            conn,
            transfer_id=transfer_id,
            account_id=from_account,
            direction=Direction.DEBIT,
            amount=amount,
            created_at=now,
        )
        insert_posting(
            conn,
            transfer_id=transfer_id,
            account_id=to_account,
            direction=Direction.CREDIT,
            amount=amount,
            created_at=now,
        )

        conn.execute("COMMIT")

    except Exception:
        # Catch-all: ensure we don't leave an open transaction on unexpected errors.
        with contextlib.suppress(sqlite3.OperationalError):
            conn.execute("ROLLBACK")
        raise

    return first_result
