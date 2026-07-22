import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.session import get_db
from app.main import app
from tests.conftest import TEST_DATABASE_URL, random_cpf, random_email


@pytest_asyncio.fixture
async def client():
    """Full-stack HTTP client wired to the test database.

    Overrides the get_db dependency so every request runs against a freshly
    created schema, exercising the real FastAPI routing/validation/auth stack.
    """
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(bind=engine, expire_on_commit=False)

    async def override_get_db():
        async with session_maker() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()
    await engine.dispose()


async def _register_verified(client: AsyncClient) -> tuple[str, str]:
    """Registers a user, verifies KYC, returns (auth_header_token, account_number)."""
    email, cpf, password = random_email(), random_cpf(), "a-strong-password"
    resp = await client.post(
        "/api/v1/auth/register", json={"email": email, "cpf": cpf, "password": password}
    )
    assert resp.status_code == 201

    resp = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200
    token = resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.post("/api/v1/dev/verify-me", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["is_verified"] is True

    resp = await client.get("/api/v1/accounts/me", headers=headers)
    assert resp.status_code == 200
    return token, resp.json()["account_number"]


@pytest.mark.asyncio
async def test_health(client: AsyncClient):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_full_deposit_and_transfer_flow(client: AsyncClient):
    alice_token, _ = await _register_verified(client)
    _, bob_account = await _register_verified(client)
    alice_headers = {"Authorization": f"Bearer {alice_token}"}

    # Alice deposits R$ 200,00
    resp = await client.post(
        "/api/v1/pix/deposit",
        headers={**alice_headers, "Idempotency-Key": str(uuid.uuid4())},
        json={"amount_cents": 20_000},
    )
    assert resp.status_code == 201
    txid = resp.json()["txid"]

    resp = await client.post(f"/api/v1/pix/deposit/{txid}/pay")
    assert resp.status_code == 200
    assert resp.json()["status"] == "COMPLETED"

    resp = await client.get("/api/v1/accounts/me", headers=alice_headers)
    assert resp.json()["balance_cents"] == 20_000

    # Alice transfers R$ 50,00 to Bob
    resp = await client.post(
        "/api/v1/transfers",
        headers={**alice_headers, "Idempotency-Key": str(uuid.uuid4())},
        json={"to_account_number": bob_account, "amount_cents": 5_000},
    )
    assert resp.status_code == 201
    assert resp.json()["status"] == "COMPLETED"

    resp = await client.get("/api/v1/accounts/me", headers=alice_headers)
    assert resp.json()["balance_cents"] == 15_000


@pytest.mark.asyncio
async def test_deposit_requires_idempotency_key(client: AsyncClient):
    token, _ = await _register_verified(client)
    resp = await client.post(
        "/api/v1/pix/deposit",
        headers={"Authorization": f"Bearer {token}"},
        json={"amount_cents": 1_000},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_unverified_user_cannot_deposit(client: AsyncClient):
    email, cpf, password = random_email(), random_cpf(), "a-strong-password"
    await client.post(
        "/api/v1/auth/register", json={"email": email, "cpf": cpf, "password": password}
    )
    login = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    token = login.json()["access_token"]

    resp = await client.post(
        "/api/v1/pix/deposit",
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": str(uuid.uuid4())},
        json={"amount_cents": 1_000},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_transfer_insufficient_balance_returns_422(client: AsyncClient):
    alice_token, _ = await _register_verified(client)
    _, bob_account = await _register_verified(client)

    resp = await client.post(
        "/api/v1/transfers",
        headers={
            "Authorization": f"Bearer {alice_token}",
            "Idempotency-Key": str(uuid.uuid4()),
        },
        json={"to_account_number": bob_account, "amount_cents": 5_000},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_login_with_wrong_password_returns_401(client: AsyncClient):
    email, cpf, password = random_email(), random_cpf(), "a-strong-password"
    await client.post(
        "/api/v1/auth/register", json={"email": email, "cpf": cpf, "password": password}
    )
    resp = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": "wrong-password"}
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_protected_route_without_token_returns_401(client: AsyncClient):
    resp = await client.get("/api/v1/accounts/me")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_deposit_then_withdraw_flow(client: AsyncClient):
    token, _ = await _register_verified(client)
    headers = {"Authorization": f"Bearer {token}"}

    # Deposit R$ 100,00
    resp = await client.post(
        "/api/v1/pix/deposit",
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
        json={"amount_cents": 10_000},
    )
    txid = resp.json()["txid"]
    await client.post(f"/api/v1/pix/deposit/{txid}/pay")

    # Withdraw R$ 30,00
    resp = await client.post(
        "/api/v1/pix/withdraw",
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
        json={"amount_cents": 3_000},
    )
    assert resp.status_code == 201
    assert resp.json()["status"] == "COMPLETED"

    resp = await client.get("/api/v1/accounts/me", headers=headers)
    assert resp.json()["balance_cents"] == 7_000


@pytest.mark.asyncio
async def test_withdraw_insufficient_balance_returns_422(client: AsyncClient):
    token, _ = await _register_verified(client)
    resp = await client.post(
        "/api/v1/pix/withdraw",
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": str(uuid.uuid4())},
        json={"amount_cents": 5_000},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_reconciliation_requires_admin_key(client: AsyncClient):
    resp = await client.get("/api/v1/admin/reconciliation")
    assert resp.status_code == 401

    resp = await client.get("/api/v1/admin/reconciliation", headers={"X-Admin-Key": "wrong-key"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_large_transfer_is_blocked_by_fraud(client: AsyncClient):
    from app.core.config import get_settings

    block_amount = get_settings().FRAUD_BLOCK_AMOUNT_CENTS
    alice_token, _ = await _register_verified(client)
    _, bob_account = await _register_verified(client)

    # Fraud screening runs before the balance check, so no funding is needed:
    # an amount at/above the block threshold is rejected outright.
    resp = await client.post(
        "/api/v1/transfers",
        headers={
            "Authorization": f"Bearer {alice_token}",
            "Idempotency-Key": str(uuid.uuid4()),
        },
        json={"to_account_number": bob_account, "amount_cents": block_amount},
    )
    assert resp.status_code == 403


async def _fund(client: AsyncClient, headers: dict, amount_cents: int) -> None:
    resp = await client.post(
        "/api/v1/pix/deposit",
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
        json={"amount_cents": amount_cents},
    )
    txid = resp.json()["txid"]
    await client.post(f"/api/v1/pix/deposit/{txid}/pay")


@pytest.mark.asyncio
async def test_medium_transfer_is_held_for_review_then_approved(client: AsyncClient):
    from app.core.config import get_settings

    settings = get_settings()
    review_amount = settings.FRAUD_REVIEW_AMOUNT_CENTS
    admin_key = settings.ADMIN_API_KEY

    alice_token, _ = await _register_verified(client)
    _, bob_account = await _register_verified(client)
    alice_headers = {"Authorization": f"Bearer {alice_token}"}
    await _fund(client, alice_headers, review_amount + 100_000)

    # A review-range amount is held (201, PENDING, fraud_status REVIEW), money unmoved.
    resp = await client.post(
        "/api/v1/transfers",
        headers={**alice_headers, "Idempotency-Key": str(uuid.uuid4())},
        json={"to_account_number": bob_account, "amount_cents": review_amount},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "PENDING"
    assert body["fraud_status"] == "REVIEW"
    held_id = body["id"]

    balance = (await client.get("/api/v1/accounts/me", headers=alice_headers)).json()[
        "balance_cents"
    ]
    assert balance == review_amount + 100_000  # unchanged while held

    # It shows up in the admin review queue.
    resp = await client.get("/api/v1/admin/fraud/reviews", headers={"X-Admin-Key": admin_key})
    assert resp.status_code == 200
    assert held_id in [item["transaction_id"] for item in resp.json()["items"]]

    # Approving it settles the transfer.
    resp = await client.post(
        f"/api/v1/admin/fraud/reviews/{held_id}/approve", headers={"X-Admin-Key": admin_key}
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "COMPLETED"

    balance = (await client.get("/api/v1/accounts/me", headers=alice_headers)).json()[
        "balance_cents"
    ]
    assert balance == 100_000


@pytest.mark.asyncio
async def test_fraud_review_queue_requires_admin_key(client: AsyncClient):
    resp = await client.get("/api/v1/admin/fraud/reviews")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_reconciliation_reports_healthy_after_real_flows(client: AsyncClient):
    from app.core.config import get_settings

    admin_key = get_settings().ADMIN_API_KEY
    alice_token, _ = await _register_verified(client)
    _, bob_account = await _register_verified(client)
    alice_headers = {"Authorization": f"Bearer {alice_token}"}

    resp = await client.post(
        "/api/v1/pix/deposit",
        headers={**alice_headers, "Idempotency-Key": str(uuid.uuid4())},
        json={"amount_cents": 20_000},
    )
    txid = resp.json()["txid"]
    await client.post(f"/api/v1/pix/deposit/{txid}/pay")
    await client.post(
        "/api/v1/transfers",
        headers={**alice_headers, "Idempotency-Key": str(uuid.uuid4())},
        json={"to_account_number": bob_account, "amount_cents": 5_000},
    )

    resp = await client.get("/api/v1/admin/reconciliation", headers={"X-Admin-Key": admin_key})
    assert resp.status_code == 200
    body = resp.json()
    assert body["is_healthy"] is True
    assert body["is_balanced"] is True
    assert body["total_debit_cents"] == body["total_credit_cents"]
    assert body["discrepancies"] == []
