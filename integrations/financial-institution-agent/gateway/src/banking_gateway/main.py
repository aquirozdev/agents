import hashlib
import importlib
import json
import logging
import uuid
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .auth import TokenVerifier
from .auth_flow import AuthFlow, build_auth_router
from .audit import record_request
from .demo_connector import DemoConnector
from .institution import InstitutionProfile, load_institution_profile
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
    Health,
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
from .ports import (
    AuthContext,
    BankingConnector,
    ConnectorError,
    IdempotencyConflictError,
    ResourceNotFoundError,
)
from .settings import Settings, get_settings, validate_runtime_settings
from .whatsapp import build_router


settings = get_settings()
validate_runtime_settings(settings)
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
institution_profile: InstitutionProfile = load_institution_profile(settings)
settings.institution_id = institution_profile.id
settings.institution_name = institution_profile.legal_name
settings.institution_locale = institution_profile.locale
settings.institution_timezone = institution_profile.timezone
settings.institution_currency = institution_profile.currency
settings.require_verified_session_for_private_data = institution_profile.require_verified_session_for_private_data
settings.verified_session_state_header = institution_profile.verified_session_state_header
validate_runtime_settings(settings)
def load_connector(runtime_settings: Settings) -> BankingConnector:
    if runtime_settings.connector == "demo":
        candidate: BankingConnector = DemoConnector()
    elif runtime_settings.connector == "fineract":
        from .fineract_connector import FineractConnector

        candidate = FineractConnector(runtime_settings)
    elif runtime_settings.connector == "canonical-http":
        from .canonical_http_connector import CanonicalHttpConnector

        candidate = CanonicalHttpConnector(runtime_settings)
    else:
        if not runtime_settings.connector_factory:
            raise RuntimeError("BANKING_CONNECTOR_FACTORY is required when BANKING_CONNECTOR=custom")
        module_name, separator, factory_name = runtime_settings.connector_factory.partition(":")
        if not separator:
            raise RuntimeError("BANKING_CONNECTOR_FACTORY must use module:factory format")
        factory: Callable[[Settings], BankingConnector] = getattr(importlib.import_module(module_name), factory_name)
        candidate = factory(runtime_settings)
    validate_connector(candidate)
    return candidate


def validate_connector(candidate: BankingConnector) -> None:
    required_methods = (
        "capabilities",
        "get_customer",
        "list_accounts",
        "list_transactions",
        "list_loans",
        "get_account",
        "list_cards",
        "list_certificates",
        "list_beneficiaries",
        "list_card_transactions",
        "get_loan",
        "get_loan_schedule",
        "list_loan_products",
        "list_savings_products",
        "list_billers",
        "get_operation",
        "create_customer_application",
        "create_loan_application",
        "create_transfer",
        "create_payment",
        "list_locations",
        "create_support_request",
    )
    missing = [name for name in required_methods if not callable(getattr(candidate, name, None))]
    if missing:
        raise RuntimeError("BankingConnector is missing required methods: " + ", ".join(missing))
    try:
        capabilities = candidate.capabilities()
    except Exception as exc:
        raise RuntimeError("BankingConnector capabilities() failed during startup") from exc
    if not isinstance(capabilities, Capabilities):
        raise RuntimeError("BankingConnector capabilities() must return Capabilities")


connector = load_connector(settings)
verifier = TokenVerifier(settings)
auth_flow = AuthFlow(settings, connector)
Authenticated = Annotated[AuthContext, Depends(verifier.verify)]
PrivateAuthenticated = Annotated[AuthContext, Depends(verifier.verify_private)]


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    close = getattr(connector, "close", None)
    if callable(close):
        close()


app = FastAPI(
    title="Canonical Banking Gateway for Dify Agents",
    version=settings.api_version,
    description="Core-agnostic gateway contract for customer-facing financial institution agents.",
    lifespan=lifespan,
    docs_url=None if settings.environment == "production" else "/docs",
    redoc_url=None if settings.environment == "production" else "/redoc",
    openapi_url=None if settings.environment == "production" else "/openapi.json",
)
app.include_router(build_auth_router(settings, auth_flow))
app.include_router(build_router(settings, auth_flow))


@app.middleware("http")
async def add_correlation_id(request: Request, call_next):
    requested_correlation_id = request.headers.get("X-Correlation-ID")
    correlation_id = (
        requested_correlation_id
        if requested_correlation_id
        and len(requested_correlation_id) <= 128
        and not any(character in requested_correlation_id for character in "\r\n")
        else uuid.uuid4().hex
    )
    request.state.correlation_id = correlation_id
    try:
        response = await call_next(request)
    except Exception:
        record_request(
            correlation_id=correlation_id,
            method=request.method,
            path=request.url.path,
            status_code=500,
            subject=getattr(request.state, "audit_subject", None),
            session_state=getattr(request.state, "session_state", None),
        )
        raise
    response.headers["X-Correlation-ID"] = correlation_id
    record_request(
        correlation_id=correlation_id,
        method=request.method,
        path=request.url.path,
        status_code=response.status_code,
        subject=getattr(request.state, "audit_subject", None),
        session_state=getattr(request.state, "session_state", None),
    )
    return response


@app.exception_handler(PermissionError)
async def permission_error_handler(request: Request, exc: PermissionError):
    return JSONResponse(
        status_code=status.HTTP_403_FORBIDDEN,
        content={
            "type": "https://errors.example.com/forbidden",
            "title": "Forbidden",
            "status": 403,
            "detail": str(exc),
            "correlationId": getattr(request.state, "correlation_id", None),
        },
        media_type="application/problem+json",
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    detail = exc.detail if isinstance(exc.detail, str) else "Request rejected"
    response = JSONResponse(
        status_code=exc.status_code,
        content={
            "type": f"https://errors.example.com/http-{exc.status_code}",
            "title": "Request rejected",
            "status": exc.status_code,
            "detail": detail,
            "correlationId": getattr(request.state, "correlation_id", None),
        },
        media_type="application/problem+json",
    )
    if exc.headers:
        response.headers.update(exc.headers)
    return response


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, _exc: RequestValidationError):
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "type": "https://errors.example.com/validation",
            "title": "Request validation failed",
            "status": 422,
            "detail": "One or more request fields are invalid",
            "correlationId": getattr(request.state, "correlation_id", None),
        },
        media_type="application/problem+json",
    )


@app.exception_handler(ResourceNotFoundError)
async def resource_not_found_handler(request: Request, exc: ResourceNotFoundError):
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND,
        content={
            "type": "https://errors.example.com/not-found",
            "title": "Not Found",
            "status": 404,
            "detail": str(exc),
            "correlationId": getattr(request.state, "correlation_id", None),
        },
        media_type="application/problem+json",
    )


@app.exception_handler(ConnectorError)
async def connector_error_handler(request: Request, exc: ConnectorError):
    return JSONResponse(
        status_code=status.HTTP_502_BAD_GATEWAY,
        content={
            "type": "https://errors.example.com/connector-unavailable",
            "title": "Institution core unavailable",
            "status": 502,
            "detail": str(exc),
            "correlationId": getattr(request.state, "correlation_id", None),
        },
        media_type="application/problem+json",
    )


@app.exception_handler(IdempotencyConflictError)
async def idempotency_conflict_handler(request: Request, exc: IdempotencyConflictError):
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content={
            "type": "https://errors.example.com/idempotency-conflict",
            "title": "Idempotency conflict",
            "status": 409,
            "detail": str(exc),
            "correlationId": getattr(request.state, "correlation_id", None),
        },
        media_type="application/problem+json",
    )


def with_request_fingerprint(context: AuthContext, payload: Any) -> AuthContext:
    """Bind a canonical request digest to an idempotent write."""

    if not hasattr(payload, "model_dump"):
        raise TypeError("Idempotent payload must be a Pydantic model")
    serialised = json.dumps(
        payload.model_dump(mode="json", by_alias=True, exclude_none=True),
        sort_keys=True,
        separators=(",", ":"),
    )
    return replace(context, request_fingerprint=hashlib.sha256(serialised.encode("utf-8")).hexdigest())


PUBLIC_CAPABILITIES = {"savings_products", "loan_products", "locations"}


def require_capability(capability: str, context: AuthContext) -> AuthContext:
    if not getattr(effective_capabilities(context), capability, False):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Capability '{capability}' is not enabled for this institution",
        )
    return context


def effective_capabilities(context: AuthContext | None = None) -> Capabilities:
    """Only expose capabilities enabled by both policy and the connector."""

    configured = institution_profile.capabilities.model_dump()
    connector_capabilities = connector.capabilities().model_dump()
    capabilities = {
        name: bool(configured.get(name, False) and connector_capabilities.get(name, False))
        for name in Capabilities.model_fields
    }
    if context is not None and context.agent_profile == "public":
        capabilities = {name: enabled and name in PUBLIC_CAPABILITIES for name, enabled in capabilities.items()}
    return Capabilities(**capabilities)


def require_idempotency(context: AuthContext) -> AuthContext:
    if not context.idempotency_key:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Idempotency-Key is required")
    if not 8 <= len(context.idempotency_key) <= 128:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Idempotency-Key must contain between 8 and 128 characters",
        )
    return context


@app.get("/v1/health", response_model=Health, tags=["Platform"], operation_id="health")
def health(request: Request) -> Health:
    return Health(
        status="ok",
        version=settings.api_version,
        correlationId=getattr(request.state, "correlation_id", None),
    )


@app.get("/v1/capabilities", response_model=Capabilities, tags=["Platform"], operation_id="getCapabilities")
def capabilities(context: Authenticated) -> Capabilities:
    return effective_capabilities(context)


@app.get("/v1/me", response_model=Customer, tags=["Customers"], operation_id="getCurrentCustomer")
def get_current_customer(context: PrivateAuthenticated) -> Customer:
    require_capability("customer_profile", context)
    current = getattr(connector, "get_current_customer", None)
    if callable(current):
        return current(context)
    return connector.get_customer(context.subject, context)


@app.get(
    "/v1/me/accounts",
    response_model=list[Account],
    tags=["Accounts"],
    operation_id="listCurrentCustomerAccounts",
)
def list_current_customer_accounts(context: PrivateAuthenticated) -> list[Account]:
    require_capability("accounts", context)
    current = getattr(connector, "list_current_customer_accounts", None)
    if callable(current):
        return current(context)
    return connector.list_accounts(context.subject, context)


@app.get(
    "/v1/me/transactions",
    response_model=list[Transaction],
    tags=["Accounts"],
    operation_id="listCurrentCustomerTransactions",
)
def list_current_customer_transactions(
    context: PrivateAuthenticated,
    limit: int = Query(default=10, ge=1, le=10),
    from_date: datetime | None = Query(default=None, alias="from"),
    to_date: datetime | None = Query(default=None, alias="to"),
) -> list[Transaction]:
    require_capability("account_transactions", context)
    current = getattr(connector, "list_current_customer_transactions", None)
    if callable(current):
        return current(context, limit, from_date, to_date)
    return connector.list_transactions(context.subject, context, limit, from_date, to_date)


@app.get("/v1/me/loans", response_model=list[Loan], tags=["Loans"], operation_id="listCurrentCustomerLoans")
def list_current_customer_loans(
    context: PrivateAuthenticated, status_filter: str | None = Query(default=None, alias="status")
) -> list[Loan]:
    require_capability("loans", context)
    current = getattr(connector, "list_current_customer_loans", None)
    if callable(current):
        return current(context, status_filter)
    return connector.list_loans(context.subject, context, status_filter)


@app.get(
    "/v1/me/cards",
    response_model=list[Card],
    tags=["Accounts"],
    operation_id="listCurrentCustomerCards",
)
def list_current_customer_cards(context: PrivateAuthenticated) -> list[Card]:
    require_capability("cards", context)
    current = getattr(connector, "list_current_customer_cards", None)
    if callable(current):
        return current(context)
    return connector.list_cards(context.subject, context)


@app.get(
    "/v1/me/certificates",
    response_model=list[Certificate],
    tags=["Accounts"],
    operation_id="listCurrentCustomerCertificates",
)
def list_current_customer_certificates(context: PrivateAuthenticated) -> list[Certificate]:
    require_capability("certificates", context)
    current = getattr(connector, "list_current_customer_certificates", None)
    if callable(current):
        return current(context)
    return connector.list_certificates(context.subject, context)


@app.get(
    "/v1/me/beneficiaries",
    response_model=list[Beneficiary],
    tags=["Payments"],
    operation_id="listCurrentCustomerBeneficiaries",
)
def list_current_customer_beneficiaries(context: PrivateAuthenticated) -> list[Beneficiary]:
    require_capability("beneficiaries", context)
    current = getattr(connector, "list_current_customer_beneficiaries", None)
    if callable(current):
        return current(context)
    return connector.list_beneficiaries(context.subject, context)


@app.get("/v1/customers/{customer_id}", response_model=Customer, tags=["Customers"], operation_id="getCustomer")
def get_customer(customer_id: str, context: PrivateAuthenticated) -> Customer:
    require_capability("customer_profile", context)
    return connector.get_customer(customer_id, context)


@app.get(
    "/v1/customers/{customer_id}/accounts",
    response_model=list[Account],
    tags=["Accounts"],
    operation_id="listCustomerAccounts",
)
def list_customer_accounts(customer_id: str, context: PrivateAuthenticated) -> list[Account]:
    require_capability("accounts", context)
    return connector.list_accounts(customer_id, context)


@app.get(
    "/v1/customers/{customer_id}/transactions",
    response_model=list[Transaction],
    tags=["Accounts"],
    operation_id="listCustomerTransactions",
)
def list_customer_transactions(
    customer_id: str,
    context: PrivateAuthenticated,
    limit: int = Query(default=10, ge=1, le=10),
    from_date: datetime | None = Query(default=None, alias="from"),
    to_date: datetime | None = Query(default=None, alias="to"),
) -> list[Transaction]:
    require_capability("account_transactions", context)
    return connector.list_transactions(customer_id, context, limit, from_date, to_date)


@app.get(
    "/v1/customers/{customer_id}/loans",
    response_model=list[Loan],
    tags=["Loans"],
    operation_id="listCustomerLoans",
)
def list_customer_loans(
    customer_id: str, context: PrivateAuthenticated, status_filter: str | None = Query(default=None, alias="status")
) -> list[Loan]:
    require_capability("loans", context)
    return connector.list_loans(customer_id, context, status_filter)


@app.get("/v1/accounts/{account_id}", response_model=Account, tags=["Accounts"], operation_id="getAccount")
def get_account(account_id: str, context: PrivateAuthenticated) -> Account:
    require_capability("accounts", context)
    return connector.get_account(account_id, context)


@app.get(
    "/v1/customers/{customer_id}/cards",
    response_model=list[Card],
    tags=["Accounts"],
    operation_id="listCustomerCards",
)
def list_customer_cards(customer_id: str, context: PrivateAuthenticated) -> list[Card]:
    require_capability("cards", context)
    return connector.list_cards(customer_id, context)


@app.get(
    "/v1/customers/{customer_id}/certificates",
    response_model=list[Certificate],
    tags=["Accounts"],
    operation_id="listCustomerCertificates",
)
def list_customer_certificates(customer_id: str, context: PrivateAuthenticated) -> list[Certificate]:
    require_capability("certificates", context)
    return connector.list_certificates(customer_id, context)


@app.get(
    "/v1/customers/{customer_id}/beneficiaries",
    response_model=list[Beneficiary],
    tags=["Payments"],
    operation_id="listCustomerBeneficiaries",
)
def list_customer_beneficiaries(customer_id: str, context: PrivateAuthenticated) -> list[Beneficiary]:
    require_capability("beneficiaries", context)
    return connector.list_beneficiaries(customer_id, context)


@app.get(
    "/v1/cards/{card_id}/transactions",
    response_model=list[Transaction],
    tags=["Accounts"],
    operation_id="listCardTransactions",
)
def list_card_transactions(
    card_id: str,
    context: PrivateAuthenticated,
    limit: int = Query(default=10, ge=1, le=10),
    from_date: datetime | None = Query(default=None, alias="from"),
    to_date: datetime | None = Query(default=None, alias="to"),
) -> list[Transaction]:
    require_capability("cards", context)
    return connector.list_card_transactions(card_id, context, limit, from_date, to_date)


@app.get("/v1/loans/{loan_id}", response_model=Loan, tags=["Loans"], operation_id="getLoan")
def get_loan(loan_id: str, context: PrivateAuthenticated) -> Loan:
    require_capability("loans", context)
    return connector.get_loan(loan_id, context)


@app.get(
    "/v1/loans/{loan_id}/schedule",
    response_model=list[Installment],
    tags=["Loans"],
    operation_id="getLoanSchedule",
)
def get_loan_schedule(loan_id: str, context: PrivateAuthenticated) -> list[Installment]:
    require_capability("loan_schedules", context)
    return connector.get_loan_schedule(loan_id, context)


@app.get("/v1/products/loans", response_model=list[Product], tags=["Loans"], operation_id="listLoanProducts")
def list_loan_products(context: Authenticated) -> list[Product]:
    require_capability("loan_products", context)
    return connector.list_loan_products()


@app.get(
    "/v1/products/savings", response_model=list[Product], tags=["Accounts"], operation_id="listSavingsProducts"
)
def list_savings_products(context: Authenticated) -> list[Product]:
    require_capability("savings_products", context)
    return connector.list_savings_products()


@app.get("/v1/billers", response_model=list[Biller], tags=["Payments"], operation_id="listBillers")
def list_billers(context: Authenticated) -> list[Biller]:
    require_capability("billers", context)
    return connector.list_billers()


@app.get(
    "/v1/operations/{operation_id}",
    response_model=OperationStatus,
    tags=["Applications"],
    operation_id="getOperationStatus",
)
def get_operation_status(operation_id: str, context: PrivateAuthenticated) -> OperationStatus:
    require_capability("operation_status", context)
    return connector.get_operation(operation_id, context)


@app.post(
    "/v1/applications/customers",
    response_model=ApplicationCreated,
    status_code=status.HTTP_201_CREATED,
    tags=["Applications"],
    operation_id="createCustomerApplication",
)
def create_customer_application(
    application: CustomerApplication, context: PrivateAuthenticated
) -> ApplicationCreated:
    require_capability("customer_applications", context)
    require_idempotency(context)
    return connector.create_customer_application(application, with_request_fingerprint(context, application))


@app.post(
    "/v1/applications/loans",
    response_model=ApplicationCreated,
    status_code=status.HTTP_201_CREATED,
    tags=["Applications"],
    operation_id="createLoanApplication",
)
def create_loan_application(application: LoanApplication, context: PrivateAuthenticated) -> ApplicationCreated:
    require_capability("loan_applications", context)
    require_idempotency(context)
    return connector.create_loan_application(application, with_request_fingerprint(context, application))


@app.post(
    "/v1/transfers",
    response_model=ApplicationCreated,
    status_code=status.HTTP_201_CREATED,
    tags=["Payments"],
    operation_id="createTransfer",
)
def create_transfer(transfer: TransferRequest, context: PrivateAuthenticated) -> ApplicationCreated:
    require_capability("transfers", context)
    require_idempotency(context)
    return connector.create_transfer(transfer, with_request_fingerprint(context, transfer))


@app.post(
    "/v1/payments",
    response_model=ApplicationCreated,
    status_code=status.HTTP_201_CREATED,
    tags=["Payments"],
    operation_id="createPayment",
)
def create_payment(payment: PaymentRequest, context: PrivateAuthenticated) -> ApplicationCreated:
    require_capability("payments", context)
    require_idempotency(context)
    return connector.create_payment(payment, with_request_fingerprint(context, payment))


@app.get("/v1/locations", response_model=list[Location], tags=["Support"], operation_id="listLocations")
def list_locations(
    context: Authenticated,
    latitude: float | None = Query(default=None, ge=-90, le=90),
    longitude: float | None = Query(default=None, ge=-180, le=180),
    location_type: str | None = Query(default=None, alias="type"),
    limit: int = Query(default=5, ge=1, le=20),
) -> list[Location]:
    require_capability("locations", context)
    return connector.list_locations(latitude, longitude, location_type, limit)


@app.post(
    "/v1/support/requests",
    response_model=ApplicationCreated,
    status_code=status.HTTP_201_CREATED,
    tags=["Support"],
    operation_id="createSupportRequest",
)
def create_support_request(request: SupportRequest, context: PrivateAuthenticated) -> ApplicationCreated:
    require_capability("support_requests", context)
    require_idempotency(context)
    return connector.create_support_request(request, with_request_fingerprint(context, request))
