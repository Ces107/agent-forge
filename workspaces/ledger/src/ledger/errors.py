"""Typed exception hierarchy for the ledger domain."""


class LedgerError(Exception):
    """Base class for all ledger domain errors."""


class InsufficientFunds(LedgerError):
    """Raised when a transfer would push the source account below its floor.

    Maps to HTTP 422 Unprocessable Entity.
    """


class IdempotencyConflict(LedgerError):
    """Raised when an idempotency key is reused with a different payload.

    Maps to HTTP 409 Conflict.
    """


class AccountNotFound(LedgerError):
    """Raised when a referenced account does not exist.

    Maps to HTTP 404 Not Found.
    """
