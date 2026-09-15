from datetime import date, datetime, timezone
from decimal import Decimal
from threading import Lock
from uuid import uuid4

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
    Money,
    OperationStatus,
    PaymentRequest,
    Product,
    SupportRequest,
    TransferRequest,
    Transaction,
)
from .ports import AuthContext, ConnectorError, IdempotencyConflictError, ResourceNotFoundError


USD = "USD"


class DemoConnector:
    """Small deterministic connector used for local demos and contract tests.

    It intentionally exposes the same port a real connector implements. It is
    not a financial ledger and must never be used for real customer data.
    """

    customer_id = "customer-demo-001"
    account_id = "account-demo-001"
    card_id = "card-demo-001"
    loan_id = "loan-demo-001"

    def __init__(self) -> None:
        self._idempotent_results: dict[str, tuple[str | None, ApplicationCreated]] = {}
        self._idempotency_lock = Lock()

    def capabilities(self) -> Capabilities:
        return Capabilities(cards=True)

    def _authorize_customer(self, customer_id: str, context: AuthContext) -> None:
        if customer_id != self.customer_id:
            raise ResourceNotFoundError("Customer not found")
        if context.subject not in {self.customer_id, "demo-operator"}:
            raise PermissionError("The session is not authorized for this customer")

    def _authorize_account(self, account_id: str, context: AuthContext) -> None:
        if account_id != self.account_id:
            raise ResourceNotFoundError("Account not found")
        if context.subject not in {self.customer_id, "demo-operator"}:
            raise PermissionError("The session is not authorized for this account")

    def _authorize_loan(self, loan_id: str, context: AuthContext) -> None:
        if loan_id != self.loan_id:
            raise ResourceNotFoundError("Loan not found")
        if context.subject not in {self.customer_id, "demo-operator"}:
            raise PermissionError("The session is not authorized for this loan")

    def _authorize_card(self, card_id: str, context: AuthContext) -> None:
        if card_id != self.card_id:
            raise ResourceNotFoundError("Card not found")
        if context.subject not in {self.customer_id, "demo-operator"}:
            raise PermissionError("The session is not authorized for this card")

    def get_customer(self, customer_id: str, context: AuthContext) -> Customer:
        self._authorize_customer(customer_id, context)
        return Customer(
            id=self.customer_id,
            displayName="María Fernanda Demo",
            status="active",
            maskedDocument="******1234",
            maskedMobile="09******78",
            branchName="Agencia Centro",
        )

    def list_accounts(self, customer_id: str, context: AuthContext) -> list[Account]:
        self._authorize_customer(customer_id, context)
        return [self._account()]

    def list_transactions(
        self,
        customer_id: str,
        context: AuthContext,
        limit: int,
        from_date: datetime | None,
        to_date: datetime | None,
    ) -> list[Transaction]:
        self._authorize_customer(customer_id, context)
        transactions = [
            Transaction(
                id="transaction-demo-001",
                postedAt=datetime(2026, 9, 12, 15, 30, tzinfo=timezone.utc),
                description="Transferencia recibida",
                amount=Money(amount=Decimal("250.00"), currency=USD),
                direction="credit",
                status="posted",
                maskedAccountNumber="****0012",
            ),
            Transaction(
                id="transaction-demo-002",
                postedAt=datetime(2026, 9, 10, 18, 10, tzinfo=timezone.utc),
                description="Compra supermercado",
                amount=Money(amount=Decimal("42.35"), currency=USD),
                direction="debit",
                status="posted",
                maskedAccountNumber="****0012",
            ),
        ]
        if from_date:
            transactions = [item for item in transactions if item.posted_at >= from_date]
        if to_date:
            transactions = [item for item in transactions if item.posted_at <= to_date]
        return transactions[: min(limit, 10)]

    def list_loans(self, customer_id: str, context: AuthContext, status: str | None) -> list[Loan]:
        self._authorize_customer(customer_id, context)
        loan = self._loan()
        return [loan] if not status or loan.status == status else []

    def get_account(self, account_id: str, context: AuthContext) -> Account:
        self._authorize_account(account_id, context)
        return self._account()

    def list_cards(self, customer_id: str, context: AuthContext) -> list[Card]:
        self._authorize_customer(customer_id, context)
        return [
            Card(
                id=self.card_id,
                maskedNumber="****7788",
                type="debit",
                name="Tarjeta débito principal",
                status="active",
                availableBalance=Money(amount=Decimal("1250.40"), currency=USD),
                currentBalance=Money(amount=Decimal("1250.40"), currency=USD),
                expiryMonth=12,
                expiryYear=2028,
            )
        ]

    def list_certificates(self, customer_id: str, context: AuthContext) -> list[Certificate]:
        self._authorize_customer(customer_id, context)
        return []

    def list_beneficiaries(self, customer_id: str, context: AuthContext) -> list[Beneficiary]:
        self._authorize_customer(customer_id, context)
        return []

    def list_card_transactions(
        self,
        card_id: str,
        context: AuthContext,
        limit: int,
        from_date: datetime | None,
        to_date: datetime | None,
    ) -> list[Transaction]:
        self._authorize_card(card_id, context)
        transactions = self.list_transactions(self.customer_id, context, 10, from_date, to_date)
        return [item.model_copy(update={"masked_account_number": "****7788"}) for item in transactions[:limit]]

    def get_loan(self, loan_id: str, context: AuthContext) -> Loan:
        self._authorize_loan(loan_id, context)
        return self._loan()

    def get_loan_schedule(self, loan_id: str, context: AuthContext) -> list[Installment]:
        self._authorize_loan(loan_id, context)
        return [
            self._installment(1, date(2026, 10, 5), "upcoming", "185.00"),
            self._installment(2, date(2026, 11, 5), "upcoming", "185.00"),
            self._installment(3, date(2026, 12, 5), "upcoming", "185.00"),
        ]

    def list_loan_products(self) -> list[Product]:
        return [
            Product(
                id="loan-product-demo",
                name="Microcrédito productivo",
                description="Financiamiento para capital de trabajo y activos productivos.",
                type="loan",
                active=True,
                currency=USD,
            )
        ]

    def list_savings_products(self) -> list[Product]:
        return [
            Product(
                id="savings-product-demo",
                name="Cuenta de ahorros",
                description="Cuenta de ahorros para personas naturales.",
                type="savings",
                active=True,
                currency=USD,
            )
        ]

    def list_billers(self) -> list[Biller]:
        return []

    def get_operation(self, operation_id: str, context: AuthContext) -> OperationStatus:
        raise ResourceNotFoundError("Operation not found")

    def create_customer_application(
        self, application: CustomerApplication, context: AuthContext
    ) -> ApplicationCreated:
        return self._created("customer-application", context)

    def create_loan_application(
        self, application: LoanApplication, context: AuthContext
    ) -> ApplicationCreated:
        self._authorize_customer(application.customer_id, context)
        return self._created("loan-application", context)

    def create_transfer(self, transfer: TransferRequest, context: AuthContext) -> ApplicationCreated:
        raise ConnectorError("Transfers are disabled in the demo connector")

    def create_payment(self, payment: PaymentRequest, context: AuthContext) -> ApplicationCreated:
        raise ConnectorError("Payments are disabled in the demo connector")

    def list_locations(
        self,
        latitude: float | None,
        longitude: float | None,
        location_type: str | None,
        limit: int,
    ) -> list[Location]:
        locations = [
            Location(
                id="branch-demo-001",
                type="branch",
                name="Agencia Centro",
                address="Av. 10 de Agosto y Colón, Quito",
                latitude=-0.1986,
                longitude=-78.4936,
                openingHours="Lunes a viernes, 08:30-16:30",
            ),
            Location(
                id="atm-demo-001",
                type="atm",
                name="Cajero Centro",
                address="Av. 10 de Agosto y Colón, Quito",
                latitude=-0.1986,
                longitude=-78.4936,
                openingHours="24 horas",
            ),
        ]
        if location_type:
            locations = [item for item in locations if item.type == location_type]
        return locations[:limit]

    def create_support_request(
        self, request: SupportRequest, context: AuthContext
    ) -> ApplicationCreated:
        return self._created("support", context)

    def _account(self) -> Account:
        return Account(
            id=self.account_id,
            maskedNumber="****0012",
            type="savings",
            name="Cuenta principal",
            status="active",
            availableBalance=Money(amount=Decimal("1250.40"), currency=USD),
            currentBalance=Money(amount=Decimal("1292.75"), currency=USD),
            updatedAt=datetime(2026, 9, 14, 14, 0, tzinfo=timezone.utc),
        )

    def _loan(self) -> Loan:
        return Loan(
            id=self.loan_id,
            productName="Microcrédito productivo",
            status="active",
            principal=Money(amount=Decimal("2500.00"), currency=USD),
            outstandingBalance=Money(amount=Decimal("1800.00"), currency=USD),
            nextPayment=self._installment(1, date(2026, 10, 5), "upcoming", "185.00"),
            maturityDate=date(2027, 12, 5),
        )

    def _installment(
        self, number: int, due_date: date, status: str, amount: str
    ) -> Installment:
        money = Money(amount=Decimal(amount), currency=USD)
        return Installment(
            number=number,
            dueDate=due_date,
            principalDue=Money(amount=Decimal("150.00"), currency=USD),
            interestDue=Money(amount=Decimal("30.00"), currency=USD),
            feesDue=Money(amount=Decimal("5.00"), currency=USD),
            totalDue=money,
            status=status,
        )

    def _created(self, prefix: str, context: AuthContext) -> ApplicationCreated:
        idempotency_key = context.idempotency_key
        if idempotency_key:
            scoped_key = f"{context.institution_id}:{context.subject}:{prefix}:{idempotency_key}"
            with self._idempotency_lock:
                existing = self._idempotent_results.get(scoped_key)
                if existing:
                    previous_fingerprint, result = existing
                    if previous_fingerprint != context.request_fingerprint:
                        raise IdempotencyConflictError(
                            "Idempotency-Key was already used with a different request"
                        )
                    return result
                result = self._new_application(prefix, context.correlation_id)
                self._idempotent_results[scoped_key] = (context.request_fingerprint, result)
                return result
        return self._new_application(prefix, context.correlation_id)

    def _new_application(self, prefix: str, correlation_id: str | None = None) -> ApplicationCreated:
        return ApplicationCreated(
            id=f"{prefix}-{uuid4().hex[:12]}",
            status="pending",
            createdAt=datetime.now(timezone.utc),
            correlationId=correlation_id or uuid4().hex,
        )
