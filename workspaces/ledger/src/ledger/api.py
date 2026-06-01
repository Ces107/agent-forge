"""Thin FastAPI layer over ledger.service.

Endpoints:
  POST /transfers                    -> TransferResult (200)
  GET  /accounts/{account_id}/balance -> {"account_id": str, "balance": int}
  GET  /reconciliation               -> reconciliation dict
"""

import os
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from ledger.errors import AccountNotFound, IdempotencyConflict, InsufficientFunds
from ledger.service import TransferResult, transfer
from ledger.store import account_balance, connect, get_account, init_schema, reconciliation

# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------

_DB_PATH_ENV = "LEDGER_DB_PATH"
_DEFAULT_DB_PATH = "ledger.db"


def create_app(db_path: str | None = None) -> FastAPI:
    """Create and configure the FastAPI application."""
    resolved_db = db_path or os.environ.get(_DB_PATH_ENV, _DEFAULT_DB_PATH)

    app = FastAPI(title="Ledger", version="0.1.0")

    # Initialise schema on startup.
    @app.on_event("startup")
    def _startup() -> None:
        conn = connect(resolved_db)
        init_schema(conn)
        conn.close()

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
        except InsufficientFunds as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except IdempotencyConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
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
