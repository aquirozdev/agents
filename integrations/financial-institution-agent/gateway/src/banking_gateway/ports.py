from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

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


AgentProfile = Literal["public", "customer"]


@dataclass(frozen=True)
class AuthContext:
    """Identity, session assurance and institution scope for connector access."""

    subject: str
    institution_id: str
    agent_profile: AgentProfile = "customer"
    session_id: str | None = None
    session_state: Literal["PUBLIC", "VERIFIED", "HUMAN_REVIEW"] = "PUBLIC"
    correlation_id: str | None = None
    idempotency_key: str | None = None
    request_fingerprint: str | None = None


class ResourceNotFoundError(LookupError):
    """The requested resource does not exist in the authorized institution."""


class ConnectorError(RuntimeError):
    """A core connector failed without exposing its internal response."""


class IdempotencyConflictError(RuntimeError):
    """The same idempotency key was reused with a different request body."""


class BankingConnector(Protocol):
    """Port implemented by each institution-specific core adapter.

    Methods receive an AuthContext so the connector can enforce resource
    ownership and institution tenancy. A connector must never trust an id from
    the conversation without checking it against this context.
    """

    def capabilities(self) -> Capabilities: ...

    def get_customer(self, customer_id: str, context: AuthContext) -> Customer: ...

    def list_accounts(self, customer_id: str, context: AuthContext) -> list[Account]: ...

    def list_transactions(
        self,
        customer_id: str,
        context: AuthContext,
        limit: int,
        from_date: datetime | None,
        to_date: datetime | None,
    ) -> list[Transaction]: ...

    def list_loans(self, customer_id: str, context: AuthContext, status: str | None) -> list[Loan]: ...

    def get_account(self, account_id: str, context: AuthContext) -> Account: ...

    def list_cards(self, customer_id: str, context: AuthContext) -> list[Card]: ...

    def list_certificates(self, customer_id: str, context: AuthContext) -> list[Certificate]: ...

    def list_beneficiaries(self, customer_id: str, context: AuthContext) -> list[Beneficiary]: ...

    def list_card_transactions(
        self,
        card_id: str,
        context: AuthContext,
        limit: int,
        from_date: datetime | None,
        to_date: datetime | None,
    ) -> list[Transaction]: ...

    def get_loan(self, loan_id: str, context: AuthContext) -> Loan: ...

    def get_loan_schedule(self, loan_id: str, context: AuthContext) -> list[Installment]: ...

    def list_loan_products(self) -> list[Product]: ...

    def list_savings_products(self) -> list[Product]: ...

    def list_billers(self) -> list[Biller]: ...

    def get_operation(self, operation_id: str, context: AuthContext) -> OperationStatus: ...

    def create_customer_application(
        self, application: CustomerApplication, context: AuthContext
    ) -> ApplicationCreated: ...

    def create_loan_application(
        self, application: LoanApplication, context: AuthContext
    ) -> ApplicationCreated: ...

    def create_transfer(self, transfer: TransferRequest, context: AuthContext) -> ApplicationCreated: ...

    def create_payment(self, payment: PaymentRequest, context: AuthContext) -> ApplicationCreated: ...

    def list_locations(
        self,
        latitude: float | None,
        longitude: float | None,
        location_type: str | None,
        limit: int,
    ) -> list[Location]: ...

    def create_support_request(
        self, request: SupportRequest, context: AuthContext
    ) -> ApplicationCreated: ...
