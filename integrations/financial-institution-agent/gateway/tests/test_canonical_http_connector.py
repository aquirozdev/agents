import json
from decimal import Decimal

import httpx
import pytest

from banking_gateway.canonical_http_connector import CanonicalHttpConnector
from banking_gateway.models import LoanApplication, Money
from banking_gateway.ports import AuthContext
from banking_gateway.settings import Settings


def _connector(handler) -> CanonicalHttpConnector:
    connector = CanonicalHttpConnector(
        Settings(upstream_base_url="https://bff.example.internal", upstream_token="bff-secret")
    )
    connector.client.close()
    connector.client = httpx.Client(
        base_url="https://bff.example.internal",
        transport=httpx.MockTransport(handler),
    )
    return connector


def test_canonical_connector_propagates_trusted_context_and_validates_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/customers/customer-001"
        assert request.headers["Authorization"] == "Bearer bff-secret"
        assert request.headers["X-Verified-Subject"] == "customer-001"
        assert request.headers["X-Verified-Session-State"] == "VERIFIED"
        assert request.headers["X-Correlation-ID"] == "corr-001"
        return httpx.Response(
            200,
            json={"id": "customer-001", "displayName": "Cliente Demo", "status": "active"},
        )

    connector = _connector(handler)
    try:
        customer = connector.get_customer(
            "customer-001",
            AuthContext(
                subject="customer-001",
                institution_id="cooperative-demo",
                session_state="VERIFIED",
                correlation_id="corr-001",
            ),
        )
        assert customer.display_name == "Cliente Demo"
    finally:
        connector.close()


def test_canonical_connector_sends_idempotent_payload() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Idempotency-Key"] == "loan-key-001"
        assert request.headers["X-Request-Fingerprint"] == "fingerprint-001"
        assert json.loads(request.content)["customerId"] == "customer-001"
        return httpx.Response(
            201,
            json={
                "id": "loan-application-001",
                "status": "pending",
                "createdAt": "2026-09-14T12:00:00Z",
            },
        )

    connector = _connector(handler)
    try:
        result = connector.create_loan_application(
            LoanApplication(
                customerId="customer-001",
                productId="loan-product-001",
                requestedAmount=Money(amount=Decimal("100"), currency="USD"),
                term=12,
                consent={"accepted": True, "version": "2026-01"},
            ),
            AuthContext(
                subject="customer-001",
                institution_id="cooperative-demo",
                session_state="VERIFIED",
                idempotency_key="loan-key-001",
                request_fingerprint="fingerprint-001",
            ),
        )
        assert result.id == "loan-application-001"
    finally:
        connector.close()


def test_canonical_connector_maps_upstream_404_to_not_found() -> None:
    connector = _connector(lambda request: httpx.Response(404, json={"detail": "hidden"}))
    try:
        with pytest.raises(LookupError):
            connector.get_customer("missing", AuthContext(subject="customer-001", institution_id="demo"))
    finally:
        connector.close()
