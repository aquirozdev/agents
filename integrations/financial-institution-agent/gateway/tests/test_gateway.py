import base64
import hashlib
import hmac
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from fastapi.testclient import TestClient

from banking_gateway.auth import TokenVerifier
from banking_gateway.demo_connector import DemoConnector
from banking_gateway.main import app, validate_connector
from banking_gateway.settings import Settings, validate_runtime_settings


client = TestClient(app)
AUTH = {"Authorization": "Bearer local-demo-token"}


def test_health_is_public() -> None:
    response = client.get("/v1/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_production_defaults_are_rejected() -> None:
    with pytest.raises(RuntimeError, match="Unsafe production configuration"):
        validate_runtime_settings(Settings(environment="production"))


def test_connector_contract_is_checked_at_startup() -> None:
    validate_connector(DemoConnector())

    with pytest.raises(RuntimeError, match="missing required methods"):
        validate_connector(object())  # type: ignore[arg-type]


def test_openapi_operation_ids_match_runtime() -> None:
    spec = yaml.safe_load((Path(__file__).resolve().parents[2] / "openapi.yaml").read_text(encoding="utf-8"))
    contract = {
        operation["operationId"]
        for path_item in spec["paths"].values()
        for operation in path_item.values()
        if isinstance(operation, dict) and "operationId" in operation
    }
    runtime = {route.operation_id for route in app.routes if getattr(route, "operation_id", None)}
    assert contract == runtime


def test_private_routes_require_bearer_token() -> None:
    response = client.get("/v1/capabilities")
    assert response.status_code == 401
    assert response.headers["content-type"].startswith("application/problem+json")


def test_local_demo_publishes_commercial_capabilities() -> None:
    response = client.get("/v1/capabilities", headers=AUTH)
    assert response.status_code == 200
    capabilities = response.json()
    assert capabilities["customerProfile"] is True
    assert capabilities["accounts"] is True
    assert capabilities["accountTransactions"] is True
    assert capabilities["loans"] is True
    assert capabilities["loanApplications"] is True
    assert capabilities["cards"] is True
    assert capabilities["transfers"] is False
    assert capabilities["payments"] is False


def test_demo_customer_accounts_follow_canonical_shape() -> None:
    response = client.get("/v1/customers/customer-demo-001/accounts", headers=AUTH)
    assert response.status_code == 200
    account = response.json()[0]
    assert account["id"] == "account-demo-001"
    assert account["availableBalance"]["currency"] == "USD"


def test_me_routes_resolve_the_authenticated_demo_customer() -> None:
    response = client.get("/v1/me/accounts", headers=AUTH)
    assert response.status_code == 200
    assert response.json()[0]["id"] == "account-demo-001"


def test_demo_cards_are_read_only_and_masked() -> None:
    response = client.get("/v1/customers/customer-demo-001/cards", headers=AUTH)
    assert response.status_code == 200
    card = response.json()[0]
    assert card["maskedNumber"] == "****7788"
    assert "number" not in card

    movements = client.get("/v1/cards/card-demo-001/transactions", headers=AUTH)
    assert movements.status_code == 200
    assert movements.json()[0]["maskedAccountNumber"] == "****7788"


def test_demo_certificates_are_not_exposed_without_institution_capability() -> None:
    response = client.get("/v1/customers/customer-demo-001/certificates", headers=AUTH)
    assert response.status_code == 404


def test_demo_connector_rejects_another_customer() -> None:
    response = client.get("/v1/customers/other-customer/accounts", headers=AUTH)
    assert response.status_code == 404


def test_loan_application_is_pending_only() -> None:
    response = client.post(
        "/v1/applications/loans",
        headers={**AUTH, "Idempotency-Key": "loan-application-001"},
        json={
            "customerId": "customer-demo-001",
            "productId": "loan-product-demo",
            "requestedAmount": {"amount": 1000, "currency": "USD"},
            "term": 12,
            "consent": {"accepted": True, "version": "2026-01"},
        },
    )
    assert response.status_code == 201
    assert response.json()["status"] == "pending"


def test_application_requires_idempotency_key() -> None:
    response = client.post(
        "/v1/applications/loans",
        headers=AUTH,
        json={
            "customerId": "customer-demo-001",
            "productId": "loan-product-demo",
            "requestedAmount": {"amount": 1000, "currency": "USD"},
            "term": 12,
            "consent": {"accepted": True, "version": "2026-01"},
        },
    )
    assert response.status_code == 400


def test_demo_does_not_expose_money_movement_capabilities() -> None:
    response = client.post(
        "/v1/transfers",
        headers={**AUTH, "Idempotency-Key": "transfer-demo-001"},
        json={
            "sourceAccountId": "account-demo-001",
            "beneficiaryId": "beneficiary-demo-001",
            "amount": {"amount": 10, "currency": "USD"},
            "consent": {"accepted": True, "version": "2026-01"},
        },
    )
    assert response.status_code == 404


def test_application_retry_with_same_key_is_safe() -> None:
    request = {
        "customerId": "customer-demo-001",
        "productId": "loan-product-demo",
        "requestedAmount": {"amount": 1000, "currency": "USD"},
        "term": 12,
        "consent": {"accepted": True, "version": "2026-01"},
    }
    headers = {**AUTH, "Idempotency-Key": "loan-application-retry-001"}
    first = client.post("/v1/applications/loans", headers=headers, json=request)
    second = client.post("/v1/applications/loans", headers=headers, json=request)
    assert first.status_code == second.status_code == 201
    assert first.json()["id"] == second.json()["id"]


def test_idempotency_key_cannot_be_reused_for_a_different_request() -> None:
    headers = {**AUTH, "Idempotency-Key": "loan-application-conflict-001"}
    first = client.post(
        "/v1/applications/loans",
        headers=headers,
        json={
            "customerId": "customer-demo-001",
            "productId": "loan-product-demo",
            "requestedAmount": {"amount": 1000, "currency": "USD"},
            "term": 12,
            "consent": {"accepted": True, "version": "2026-01"},
        },
    )
    second = client.post(
        "/v1/applications/loans",
        headers=headers,
        json={
            "customerId": "customer-demo-001",
            "productId": "loan-product-demo",
            "requestedAmount": {"amount": 1200, "currency": "USD"},
            "term": 12,
            "consent": {"accepted": True, "version": "2026-01"},
        },
    )
    assert first.status_code == 201
    assert second.status_code == 409


def test_dify_user_auth_requires_dynamic_identity_header() -> None:
    settings = Settings(auth_mode="dify-user", gateway_token="gateway-secret")
    verifier = TokenVerifier(settings)
    request = SimpleNamespace(
        headers={"Authorization": "Bearer gateway-secret"},
        state=SimpleNamespace(correlation_id="corr-1"),
    )
    try:
        verifier.verify(request)
    except Exception as error:
        assert getattr(error, "status_code", None) == 401
    else:
        raise AssertionError("dify-user mode must require X-Dify-End-User-ID")

    request.headers["X-Dify-End-User-ID"] = "whatsapp:+593999999999"
    context = verifier.verify(request)
    assert context.subject == "whatsapp:+593999999999"


def test_dify_user_auth_verifies_forwarded_identity_signature() -> None:
    settings = Settings(
        auth_mode="dify-user",
        gateway_token="gateway-secret",
        dify_identity_secret="identity-secret",
    )
    verifier = TokenVerifier(settings)
    subject = "whatsapp:+593999999999"
    request = SimpleNamespace(
        headers={
            "Authorization": "Bearer gateway-secret",
            "X-Dify-End-User-ID": subject,
            "X-Dify-End-User-Signature": "sha256="
            + hmac.new(b"identity-secret", subject.encode(), hashlib.sha256).hexdigest(),
        },
        state=SimpleNamespace(correlation_id="corr-2"),
    )

    context = verifier.verify(request)

    assert context.subject == subject

    request.headers["X-Dify-End-User-Signature"] = "sha256=invalid"
    try:
        verifier.verify(request)
    except Exception as error:
        assert getattr(error, "status_code", None) == 401
    else:
        raise AssertionError("dify-user mode must reject an invalid identity signature")


def test_dify_user_cannot_promote_itself_to_private_access() -> None:
    settings = Settings(
        auth_mode="dify-user",
        gateway_token="gateway-secret",
        dify_identity_secret="identity-secret",
    )
    verifier = TokenVerifier(settings)
    subject = "whatsapp:+593999999999"
    signature = "sha256=" + hmac.new(b"identity-secret", subject.encode(), hashlib.sha256).hexdigest()
    request = SimpleNamespace(
        headers={
            "Authorization": "Bearer gateway-secret",
            "X-Dify-End-User-ID": subject,
            "X-Dify-End-User-Signature": signature,
        },
        state=SimpleNamespace(correlation_id="corr-3"),
    )

    with pytest.raises(Exception) as error:
        verifier.verify_private(request)
    assert getattr(error.value, "status_code", None) == 403

    request.headers["X-Verified-Session-State"] = "VERIFIED"
    with pytest.raises(Exception) as error:
        verifier.verify_private(request)
    assert getattr(error.value, "status_code", None) == 403


def test_local_dify_user_assertion_can_create_a_verified_private_session() -> None:
    settings = Settings(
        auth_mode="dify-user",
        gateway_token="gateway-secret",
        dify_identity_secret="identity-secret",
        auth_verification_mode="local-acceptance",
        auth_identity_secret="auth-secret",
    )
    verifier = TokenVerifier(settings)
    subject = "101"
    expires_at = 4_000_000_000
    encoded_subject = base64.urlsafe_b64encode(subject.encode()).decode().rstrip("=")
    assertion = "banking:v1:" + encoded_subject + ":" + str(expires_at) + ":" + hmac.new(
        b"auth-secret", f"{subject}|{expires_at}".encode(), hashlib.sha256
    ).hexdigest()
    request = SimpleNamespace(
        headers={
            "Authorization": "Bearer gateway-secret",
            "X-Dify-End-User-ID": assertion,
            "X-Dify-End-User-Signature": "sha256="
            + hmac.new(b"identity-secret", assertion.encode(), hashlib.sha256).hexdigest(),
        },
        state=SimpleNamespace(correlation_id="corr-4"),
    )

    context = verifier.verify_private(request)

    assert context.subject == subject
    assert context.session_state == "VERIFIED"


def test_external_proxy_must_authenticate_and_supply_verified_state() -> None:
    settings = Settings(auth_mode="external-proxy", gateway_token="proxy-secret")
    verifier = TokenVerifier(settings)
    request = SimpleNamespace(
        headers={
            "Authorization": "Bearer wrong-secret",
            "X-Verified-Subject": "customer-001",
            "X-Verified-Session-State": "VERIFIED",
        },
        state=SimpleNamespace(correlation_id="corr-4"),
    )

    with pytest.raises(Exception) as error:
        verifier.verify_private(request)
    assert getattr(error.value, "status_code", None) == 401

    request.headers["Authorization"] = "Bearer proxy-secret"
    request.headers["X-Verified-Session-State"] = "PUBLIC"
    with pytest.raises(Exception) as error:
        verifier.verify_private(request)
    assert getattr(error.value, "status_code", None) == 403

    request.headers["X-Verified-Session-State"] = "VERIFIED"
    context = verifier.verify_private(request)
    assert context.subject == "customer-001"
