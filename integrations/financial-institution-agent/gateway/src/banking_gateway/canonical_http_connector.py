"""Connector for an institution service that already implements the canonical API."""

from datetime import datetime
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel

from .models import (
    Account,
    ApplicationCreated,
    Beneficiary,
    Biller,
    Card,
    Capabilities,
    Certificate,
    Customer,
    CustomerApplication,
    Installment,
    Loan,
    LoanApplication,
    Location,
    OperationStatus,
    PaymentRequest,
    Product,
    SupportRequest,
    TransferRequest,
    Transaction,
)
from .ports import AuthContext, ConnectorError, ResourceNotFoundError
from .settings import Settings


ModelT = TypeVar("ModelT", bound=BaseModel)


class CanonicalHttpConnector:
    """Forward the canonical contract to a private institution BFF.

    This connector is useful when the core mapping already exists in the
    institution's middleware. It never accepts URLs or resource paths from the
    model; every path is fixed in this module.
    """

    def __init__(self, settings: Settings) -> None:
        if not settings.upstream_base_url or not settings.upstream_token:
            raise RuntimeError("BANKING_UPSTREAM_BASE_URL and BANKING_UPSTREAM_TOKEN are required")
        self.settings = settings
        self.client = httpx.Client(
            base_url=settings.upstream_base_url.rstrip("/"),
            verify=settings.upstream_verify_tls,
            timeout=httpx.Timeout(
                settings.upstream_timeout_seconds,
                connect=settings.upstream_connect_timeout_seconds,
            ),
        )

    def close(self) -> None:
        self.client.close()

    def capabilities(self) -> Capabilities:
        return self._model("GET", "/v1/capabilities", Capabilities)

    def _headers(self, context: AuthContext | None = None) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self.settings.upstream_token}"}
        if context:
            if context.correlation_id:
                headers["X-Correlation-ID"] = context.correlation_id
            headers["X-Institution-ID"] = context.institution_id
            headers["X-Verified-Subject"] = context.subject
            headers["X-Verified-Session-State"] = context.session_state
            if context.session_id:
                headers["X-Session-ID"] = context.session_id
            if context.idempotency_key:
                headers["Idempotency-Key"] = context.idempotency_key
            if context.request_fingerprint:
                headers["X-Request-Fingerprint"] = context.request_fingerprint
        return headers

    def _request(
        self,
        method: str,
        path: str,
        *,
        context: AuthContext | None = None,
        params: dict[str, Any] | None = None,
        payload: BaseModel | None = None,
    ) -> Any:
        try:
            response = self.client.request(
                method,
                path,
                headers=self._headers(context),
                params=params,
                json=payload.model_dump(mode="json", by_alias=True, exclude_none=True) if payload else None,
            )
        except httpx.HTTPError as exc:
            raise ConnectorError("The institution BFF could not be reached") from exc
        if response.status_code == 404:
            raise ResourceNotFoundError("Resource not found")
        if response.status_code in {401, 403}:
            raise PermissionError("The institution BFF rejected the session")
        if response.status_code >= 400:
            raise ConnectorError("The institution BFF rejected the request")
        try:
            return response.json()
        except ValueError as exc:
            raise ConnectorError("The institution BFF returned invalid JSON") from exc

    def _model(self, method: str, path: str, model: type[ModelT], **kwargs: Any) -> ModelT:
        try:
            return model.model_validate(self._request(method, path, **kwargs))
        except (ConnectorError, ResourceNotFoundError, PermissionError):
            raise
        except Exception as exc:
            raise ConnectorError("The institution BFF returned an invalid contract response") from exc

    def _models(self, method: str, path: str, model: type[ModelT], **kwargs: Any) -> list[ModelT]:
        try:
            raw = self._request(method, path, **kwargs)
            if not isinstance(raw, list):
                raise ValueError("Expected a JSON array")
            return [model.model_validate(item) for item in raw]
        except (ConnectorError, ResourceNotFoundError, PermissionError):
            raise
        except Exception as exc:
            raise ConnectorError("The institution BFF returned an invalid contract response") from exc

    def get_customer(self, customer_id: str, context: AuthContext) -> Customer:
        return self._model("GET", f"/v1/customers/{customer_id}", Customer, context=context)

    def get_current_customer(self, context: AuthContext) -> Customer:
        return self._model("GET", "/v1/me", Customer, context=context)

    def list_accounts(self, customer_id: str, context: AuthContext) -> list[Account]:
        return self._models("GET", f"/v1/customers/{customer_id}/accounts", Account, context=context)

    def list_current_customer_accounts(self, context: AuthContext) -> list[Account]:
        return self._models("GET", "/v1/me/accounts", Account, context=context)

    def list_transactions(
        self,
        customer_id: str,
        context: AuthContext,
        limit: int,
        from_date: datetime | None,
        to_date: datetime | None,
    ) -> list[Transaction]:
        params: dict[str, Any] = {"limit": limit}
        if from_date:
            params["from"] = from_date.isoformat()
        if to_date:
            params["to"] = to_date.isoformat()
        return self._models("GET", f"/v1/customers/{customer_id}/transactions", Transaction, context=context, params=params)

    def list_current_customer_transactions(
        self,
        context: AuthContext,
        limit: int,
        from_date: datetime | None,
        to_date: datetime | None,
    ) -> list[Transaction]:
        params: dict[str, Any] = {"limit": limit}
        if from_date:
            params["from"] = from_date.isoformat()
        if to_date:
            params["to"] = to_date.isoformat()
        return self._models("GET", "/v1/me/transactions", Transaction, context=context, params=params)

    def list_loans(self, customer_id: str, context: AuthContext, status: str | None) -> list[Loan]:
        params = {"status": status} if status else None
        return self._models("GET", f"/v1/customers/{customer_id}/loans", Loan, context=context, params=params)

    def list_current_customer_loans(self, context: AuthContext, status: str | None) -> list[Loan]:
        params = {"status": status} if status else None
        return self._models("GET", "/v1/me/loans", Loan, context=context, params=params)

    def get_account(self, account_id: str, context: AuthContext) -> Account:
        return self._model("GET", f"/v1/accounts/{account_id}", Account, context=context)

    def list_cards(self, customer_id: str, context: AuthContext) -> list[Card]:
        return self._models("GET", f"/v1/customers/{customer_id}/cards", Card, context=context)

    def list_current_customer_cards(self, context: AuthContext) -> list[Card]:
        return self._models("GET", "/v1/me/cards", Card, context=context)

    def list_certificates(self, customer_id: str, context: AuthContext) -> list[Certificate]:
        return self._models("GET", f"/v1/customers/{customer_id}/certificates", Certificate, context=context)

    def list_current_customer_certificates(self, context: AuthContext) -> list[Certificate]:
        return self._models("GET", "/v1/me/certificates", Certificate, context=context)

    def list_beneficiaries(self, customer_id: str, context: AuthContext) -> list[Beneficiary]:
        return self._models("GET", f"/v1/customers/{customer_id}/beneficiaries", Beneficiary, context=context)

    def list_current_customer_beneficiaries(self, context: AuthContext) -> list[Beneficiary]:
        return self._models("GET", "/v1/me/beneficiaries", Beneficiary, context=context)

    def list_card_transactions(
        self,
        card_id: str,
        context: AuthContext,
        limit: int,
        from_date: datetime | None,
        to_date: datetime | None,
    ) -> list[Transaction]:
        params: dict[str, Any] = {"limit": limit}
        if from_date:
            params["from"] = from_date.isoformat()
        if to_date:
            params["to"] = to_date.isoformat()
        return self._models("GET", f"/v1/cards/{card_id}/transactions", Transaction, context=context, params=params)

    def get_loan(self, loan_id: str, context: AuthContext) -> Loan:
        return self._model("GET", f"/v1/loans/{loan_id}", Loan, context=context)

    def get_loan_schedule(self, loan_id: str, context: AuthContext) -> list[Installment]:
        return self._models("GET", f"/v1/loans/{loan_id}/schedule", Installment, context=context)

    def list_loan_products(self) -> list[Product]:
        return self._models("GET", "/v1/products/loans", Product)

    def list_savings_products(self) -> list[Product]:
        return self._models("GET", "/v1/products/savings", Product)

    def list_billers(self) -> list[Biller]:
        return self._models("GET", "/v1/billers", Biller)

    def get_operation(self, operation_id: str, context: AuthContext) -> OperationStatus:
        return self._model("GET", f"/v1/operations/{operation_id}", OperationStatus, context=context)

    def create_customer_application(
        self, application: CustomerApplication, context: AuthContext
    ) -> ApplicationCreated:
        return self._model("POST", "/v1/applications/customers", ApplicationCreated, context=context, payload=application)

    def create_loan_application(self, application: LoanApplication, context: AuthContext) -> ApplicationCreated:
        return self._model("POST", "/v1/applications/loans", ApplicationCreated, context=context, payload=application)

    def create_transfer(self, transfer: TransferRequest, context: AuthContext) -> ApplicationCreated:
        return self._model("POST", "/v1/transfers", ApplicationCreated, context=context, payload=transfer)

    def create_payment(self, payment: PaymentRequest, context: AuthContext) -> ApplicationCreated:
        return self._model("POST", "/v1/payments", ApplicationCreated, context=context, payload=payment)

    def list_locations(
        self,
        latitude: float | None,
        longitude: float | None,
        location_type: str | None,
        limit: int,
    ) -> list[Location]:
        params: dict[str, Any] = {"limit": limit}
        if latitude is not None:
            params["latitude"] = latitude
        if longitude is not None:
            params["longitude"] = longitude
        if location_type:
            params["type"] = location_type
        return self._models("GET", "/v1/locations", Location, params=params)

    def create_support_request(self, request: SupportRequest, context: AuthContext) -> ApplicationCreated:
        return self._model("POST", "/v1/support/requests", ApplicationCreated, context=context, payload=request)
