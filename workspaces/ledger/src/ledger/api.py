"""Thin FastAPI layer over ledger.service.

Endpoints:
  POST /accounts                      -> {"account_id": str, "balance": int} (201)
  POST /transfers                     -> TransferResult (200)
  GET  /accounts/{account_id}/balance -> {"account_id": str, "balance": int}
  GET  /reconciliation                -> reconciliation dict
"""

import os
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from ledger.errors import (
    AccountNotFound,
    IdempotencyConflict,
    InsufficientFunds,
    InvalidAmount,
    ServiceUnavailable,
)
from ledger.service import TransferResult, transfer
from ledger.store import (
    account_balance,
    connect,
    create_account,
    get_account,
    init_schema,
    reconciliation,
)

# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------

_DB_PATH_ENV = "LEDGER_DB_PATH"
_DEFAULT_DB_PATH = "ledger.db"


def create_app(db_path: str | None = None) -> FastAPI:
    """Create and configure the FastAPI application."""
    resolved_db = db_path or os.environ.get(_DB_PATH_ENV, _DEFAULT_DB_PATH)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        # Initialise schema on startup (lifespan replaces the deprecated on_event).
        conn = connect(resolved_db)
        init_schema(conn)
        conn.close()
        yield

    app = FastAPI(title="Ledger", version="0.1.0", lifespan=lifespan)

    # ---------- routes ----------

    class TransferRequest(BaseModel):
        idempotency_key: str = Field(..., min_length=1)
        from_account: str = Field(..., min_length=1)
        to_account: str = Field(..., min_length=1)
        amount: int = Field(..., gt=0, description="Amount in minor units (integer, >0)")

    class TransferResponse(BaseModel):
        transfer_id: str
        idempotency_key: str
        from_account: str
        to_account: str
        amount: int
        replayed: bool

    class CreateAccountRequest(BaseModel):
        account_id: str = Field(..., min_length=1)
        balance_floor: int = Field(0, description="Minimum allowed balance (default 0).")

    class AccountResponse(BaseModel):
        account_id: str
        balance: int

    @app.post("/accounts", response_model=AccountResponse, status_code=201)
    def post_account(body: CreateAccountRequest) -> AccountResponse:
        conn = connect(resolved_db)
        try:
            try:
                create_account(conn, body.account_id, balance_floor=body.balance_floor)
            except sqlite3.IntegrityError as exc:
                raise HTTPException(
                    status_code=409, detail=f"Account already exists: {body.account_id!r}"
                ) from exc
            balance = account_balance(conn, body.account_id)
        finally:
            conn.close()
        return AccountResponse(account_id=body.account_id, balance=balance)

    @app.post("/transfers", response_model=TransferResponse)
    def post_transfer(body: TransferRequest) -> TransferResponse:
        conn = connect(resolved_db)
        try:
            result: TransferResult = transfer(
                conn,
                idempotency_key=body.idempotency_key,
                from_account=body.from_account,
                to_account=body.to_account,
                amount=body.amount,
            )
        except AccountNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (InsufficientFunds, InvalidAmount) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except IdempotencyConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ServiceUnavailable as exc:
            raise HTTPException(
                status_code=503, headers={"Retry-After": "1"}, detail=str(exc)
            ) from exc
        finally:
            conn.close()

        return TransferResponse(
            transfer_id=result.transfer_id,
            idempotency_key=result.idempotency_key,
            from_account=result.from_account,
            to_account=result.to_account,
            amount=result.amount,
            replayed=result.replayed,
        )

    @app.get("/accounts/{account_id}/balance")
    def get_balance(account_id: str) -> dict[str, Any]:
        conn = connect(resolved_db)
        try:
            row = get_account(conn, account_id)
            if row is None:
                raise HTTPException(status_code=404, detail=f"Account not found: {account_id!r}")
            balance = account_balance(conn, account_id)
        finally:
            conn.close()
        return {"account_id": account_id, "balance": balance}

    @app.get("/reconciliation")
    def get_reconciliation() -> dict[str, Any]:
        conn = connect(resolved_db)
        try:
            result = reconciliation(conn)
        finally:
            conn.close()
        return result

    return app


# Default app instance (for uvicorn / direct import).
app = create_app()
