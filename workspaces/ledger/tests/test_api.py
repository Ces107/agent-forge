"""API-layer tests using FastAPI TestClient.

Covers all routes and error paths in src/ledger/api.py:
  - POST /transfers: success, idempotent replay, AccountNotFound (404), InsufficientFunds (422),
    InvalidAmount (422), IdempotencyConflict (409), ServiceUnavailable (503)
  - GET  /accounts/{id}/balance: success, 404 on unknown account
  - GET  /reconciliation: balanced result
"""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from ledger.api import create_app
from ledger.service import transfer as svc_transfer
from ledger.store import connect, create_account, init_schema

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_db(tmp_path: object) -> str:
    """Path to a freshly-initialised temp-file SQLite database."""
    import pathlib
    db_file = pathlib.Path(str(tmp_path)) / "test_ledger.db"
    conn = connect(str(db_file))
    init_schema(conn)
    conn.close()
    return str(db_file)


@pytest.fixture()
def client(tmp_db: str) -> TestClient:
    """TestClient backed by an isolated DB."""
    app = create_app(db_path=tmp_db)
    return TestClient(app)


@pytest.fixture()
def seeded_client(tmp_db: str) -> tuple[TestClient, str]:
    """TestClient with alice (balance 1000) and bob (balance 0) pre-seeded."""
    conn = connect(tmp_db)
    bank = "__bank__alice"
    create_account(conn, bank, balance_floor=-(10**12))
    create_account(conn, "alice")
    create_account(conn, "bob")
    # Seed alice with 1000 via a direct service transfer (bypasses API for setup only)
    svc_transfer(
        conn,
        idempotency_key="seed-alice",
        from_account=bank,
        to_account="alice",
        amount=1000,
    )
    conn.close()
    app = create_app(db_path=tmp_db)
    return TestClient(app), tmp_db


# ---------------------------------------------------------------------------
# POST /accounts — account creation (makes the API runnable end-to-end)
# ---------------------------------------------------------------------------


def test_post_account_creates_account_with_zero_balance(client: TestClient) -> None:
    resp = client.post("/accounts", json={"account_id": "carol"})
    assert resp.status_code == 201
    assert resp.json() == {"account_id": "carol", "balance": 0}
    # And the balance endpoint now finds it.
    assert client.get("/accounts/carol/balance").json()["balance"] == 0


def test_post_account_duplicate_returns_409(client: TestClient) -> None:
    assert client.post("/accounts", json={"account_id": "dave"}).status_code == 201
    dup = client.post("/accounts", json={"account_id": "dave"})
    assert dup.status_code == 409


def test_api_is_runnable_end_to_end(client: TestClient) -> None:
    """The whole demo via HTTP only: create a funding account, an account, move money, reconcile."""
    bank = client.post("/accounts", json={"account_id": "bank", "balance_floor": -10**12})
    assert bank.status_code == 201
    assert client.post("/accounts", json={"account_id": "erin"}).status_code == 201
    moved = client.post("/transfers", json={
        "idempotency_key": "fund-erin", "from_account": "bank", "to_account": "erin", "amount": 500,
    })
    assert moved.status_code == 200
    assert client.get("/accounts/erin/balance").json()["balance"] == 500
    recon = client.get("/reconciliation").json()
    assert recon["balanced"] is True
    assert recon["total_debits"] == recon["total_credits"]


# ---------------------------------------------------------------------------
# POST /transfers — happy path
# ---------------------------------------------------------------------------


def test_post_transfer_success(seeded_client: tuple[TestClient, str]) -> None:
    client, _ = seeded_client
    resp = client.post("/transfers", json={
        "idempotency_key": "txn-api-001",
        "from_account": "alice",
        "to_account": "bob",
        "amount": 200,
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["from_account"] == "alice"
    assert body["to_account"] == "bob"
    assert body["amount"] == 200
    assert body["replayed"] is False
    assert body["idempotency_key"] == "txn-api-001"
    assert body["transfer_id"]


# ---------------------------------------------------------------------------
# POST /transfers — idempotent replay
# ---------------------------------------------------------------------------


def test_post_transfer_replay_returns_replayed_true(seeded_client: tuple[TestClient, str]) -> None:
    client, _ = seeded_client
    payload = {
        "idempotency_key": "txn-replay-001",
        "from_account": "alice",
        "to_account": "bob",
        "amount": 100,
    }
    first = client.post("/transfers", json=payload)
    assert first.status_code == 200
    assert first.json()["replayed"] is False

    second = client.post("/transfers", json=payload)
    assert second.status_code == 200
    body = second.json()
    assert body["replayed"] is True
    assert body["transfer_id"] == first.json()["transfer_id"]


def test_post_transfer_replay_no_new_postings(seeded_client: tuple[TestClient, str]) -> None:
    client, db_path = seeded_client
    payload = {
        "idempotency_key": "txn-replay-002",
        "from_account": "alice",
        "to_account": "bob",
        "amount": 50,
    }
    first = client.post("/transfers", json=payload)
    transfer_id = first.json()["transfer_id"]

    conn = connect(db_path)
    count_before = conn.execute(
        "SELECT COUNT(*) FROM postings WHERE transfer_id=?", (transfer_id,)
    ).fetchone()[0]

    client.post("/transfers", json=payload)

    count_after = conn.execute(
        "SELECT COUNT(*) FROM postings WHERE transfer_id=?", (transfer_id,)
    ).fetchone()[0]
    conn.close()

    assert count_before == count_after == 2


# ---------------------------------------------------------------------------
# POST /transfers — AccountNotFound (404)
# ---------------------------------------------------------------------------


def test_post_transfer_unknown_from_account_returns_404(client: TestClient) -> None:
    resp = client.post("/transfers", json={
        "idempotency_key": "notfound-from",
        "from_account": "ghost",
        "to_account": "also-ghost",
        "amount": 10,
    })
    assert resp.status_code == 404


def test_post_transfer_unknown_to_account_404(seeded_client: tuple[TestClient, str]) -> None:
    client, _ = seeded_client
    resp = client.post("/transfers", json={
        "idempotency_key": "notfound-to",
        "from_account": "alice",
        "to_account": "nobody",
        "amount": 10,
    })
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /transfers — InsufficientFunds (422)
# ---------------------------------------------------------------------------


def test_post_transfer_insufficient_funds_422(seeded_client: tuple[TestClient, str]) -> None:
    client, _ = seeded_client
    resp = client.post("/transfers", json={
        "idempotency_key": "overdraft-api",
        "from_account": "alice",
        "to_account": "bob",
        "amount": 99999,
    })
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# POST /transfers — InvalidAmount via Pydantic (422) and service (422)
# ---------------------------------------------------------------------------


def test_post_transfer_zero_amount_returns_422_pydantic(client: TestClient) -> None:
    """Pydantic Field(gt=0) rejects amount=0 before the service layer."""
    resp = client.post("/transfers", json={
        "idempotency_key": "invalid-zero",
        "from_account": "alice",
        "to_account": "bob",
        "amount": 0,
    })
    assert resp.status_code == 422


def test_post_transfer_negative_amount_returns_422_pydantic(client: TestClient) -> None:
    """Pydantic Field(gt=0) rejects negative amounts before the service layer."""
    resp = client.post("/transfers", json={
        "idempotency_key": "invalid-neg",
        "from_account": "alice",
        "to_account": "bob",
        "amount": -5,
    })
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# POST /transfers — IdempotencyConflict (409)
# ---------------------------------------------------------------------------


def test_post_transfer_idempotency_conflict_409(seeded_client: tuple[TestClient, str]) -> None:
    client, _ = seeded_client
    # First call with key + payload A
    first_resp = client.post("/transfers", json={
        "idempotency_key": "conflict-key-api",
        "from_account": "alice",
        "to_account": "bob",
        "amount": 10,
    })
    assert first_resp.status_code == 200

    # Second call: same key, different amount -> conflict
    resp = client.post("/transfers", json={
        "idempotency_key": "conflict-key-api",
        "from_account": "alice",
        "to_account": "bob",
        "amount": 99,
    })
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# POST /transfers — ServiceUnavailable (503) via lock contention
# ---------------------------------------------------------------------------


def test_post_transfer_service_unavailable_returns_503(
    seeded_client: tuple[TestClient, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Simulate lock contention: hold BEGIN IMMEDIATE on a second connection,
    patch busy_timeout to 1 ms so the service exhausts retries quickly,
    assert the API returns 503 with a Retry-After header.
    """
    client_obj, db_path = seeded_client

    # Hold the write lock
    blocker = sqlite3.connect(db_path, isolation_level=None)
    blocker.execute("PRAGMA journal_mode=WAL;")
    blocker.execute("BEGIN IMMEDIATE")

    import ledger.store as store_mod

    original_apply = store_mod._apply_pragmas  # noqa: SLF001

    def patched_apply(conn: sqlite3.Connection) -> None:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.execute("PRAGMA busy_timeout=1;")
        conn.execute("PRAGMA synchronous=NORMAL;")

    monkeypatch.setattr(store_mod, "_apply_pragmas", patched_apply)

    try:
        resp = client_obj.post("/transfers", json={
            "idempotency_key": "busy-api-001",
            "from_account": "alice",
            "to_account": "bob",
            "amount": 5,
        })
        assert resp.status_code == 503
        assert "Retry-After" in resp.headers
    finally:
        blocker.execute("ROLLBACK")
        blocker.close()
        monkeypatch.setattr(store_mod, "_apply_pragmas", original_apply)


# ---------------------------------------------------------------------------
# GET /accounts/{id}/balance — success
# ---------------------------------------------------------------------------


def test_get_balance_returns_correct_balance(seeded_client: tuple[TestClient, str]) -> None:
    client, _ = seeded_client
    resp = client.get("/accounts/alice/balance")
    assert resp.status_code == 200
    body = resp.json()
    assert body["account_id"] == "alice"
    assert body["balance"] == 1000


def test_get_balance_bob_is_zero(seeded_client: tuple[TestClient, str]) -> None:
    client, _ = seeded_client
    resp = client.get("/accounts/bob/balance")
    assert resp.status_code == 200
    assert resp.json()["balance"] == 0


# ---------------------------------------------------------------------------
# GET /accounts/{id}/balance — 404 on unknown account
# ---------------------------------------------------------------------------


def test_get_balance_unknown_account_returns_404(client: TestClient) -> None:
    resp = client.get("/accounts/nobody/balance")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /reconciliation — balanced
# ---------------------------------------------------------------------------


def test_get_reconciliation_balanced(seeded_client: tuple[TestClient, str]) -> None:
    client, _ = seeded_client
    # Make a transfer first
    client.post("/transfers", json={
        "idempotency_key": "rec-api-001",
        "from_account": "alice",
        "to_account": "bob",
        "amount": 300,
    })
    resp = client.get("/reconciliation")
    assert resp.status_code == 200
    body = resp.json()
    assert body["balanced"] is True
    assert body["total_debits"] == body["total_credits"]
    assert "accounts" in body


def test_get_reconciliation_empty_db(client: TestClient) -> None:
    """An empty ledger must also report balanced (0 == 0)."""
    resp = client.get("/reconciliation")
    assert resp.status_code == 200
    body = resp.json()
    assert body["balanced"] is True
    assert body["total_debits"] == 0
    assert body["total_credits"] == 0
