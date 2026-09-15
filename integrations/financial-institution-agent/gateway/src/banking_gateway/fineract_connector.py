"""Optional Apache Fineract adapter for the local core demo.

This module is intentionally outside the canonical contract. It translates a
small read-only subset of Fineract resources into the same models used by any
institution connector. It is not required by the reusable agent template.
"""

import re
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from .models import (
    Account,
    ApplicationCreated,
    Beneficiary,
    Biller,
    Capabilities,
    Card,
    Customer,
    CustomerApplication,
    Certificate,
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
from .ports import AuthContext, ConnectorError, ResourceNotFoundError
from .settings import Settings


class FineractConnector:
    """Read-only customer connector for the optional Fineract demo."""

    def __init__(self, settings: Settings) -> None:
        if not settings.fineract_username or not settings.fineract_password:
            raise RuntimeError("BANKING_FINERACT_USERNAME and BANKING_FINERACT_PASSWORD are required")
        self.client = httpx.Client(
            base_url=settings.fineract_base_url.rstrip("/"),
            auth=(settings.fineract_username, settings.fineract_password),
            headers={"Fineract-Platform-TenantId": settings.fineract_tenant},
            verify=settings.fineract_verify_tls,
            timeout=httpx.Timeout(15.0, connect=5.0),
        )

    def close(self) -> None:
        self.client.close()

    def capabilities(self) -> Capabilities:
        return Capabilities(
            customer_profile=True,
            accounts=True,
            account_transactions=True,
            loans=True,
            savings_products=True,
            loan_products=True,
            loan_schedules=True,
            cards=False,
            customer_applications=False,
            loan_applications=False,
            locations=False,
            support_requests=False,
        )

    def _get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        context: AuthContext | None = None,
    ) -> Any:
        headers = {}
        if context:
            if context.correlation_id:
                headers["X-Correlation-ID"] = context.correlation_id
            headers["X-Institution-ID"] = context.institution_id
        try:
            response = self.client.get(path, params=params, headers=headers)
        except httpx.HTTPError as exc:
            raise ConnectorError("The institution core could not be reached") from exc
        if response.status_code == 404:
            raise ResourceNotFoundError("Resource not found")
        if response.status_code >= 400:
            raise ConnectorError("The institution core rejected the request")
        try:
            return response.json()
        except ValueError as exc:
            raise ConnectorError("The institution core returned invalid JSON") from exc

    def _customer_id(self, customer_id: str, context: AuthContext) -> int:
        if context.subject != "demo-operator" and context.subject != customer_id:
            raise PermissionError("The session is not authorized for this customer")
        try:
            return int(customer_id)
        except ValueError as exc:
            raise ResourceNotFoundError("Customer not found") from exc

    def resolve_customer_identifier(self, identifier: str) -> str | None:
        """Resolve a registered mobile, email or external id to a Fineract client."""

        normalized = self._normalize_identifier(identifier)
        if not normalized:
            return None
        raw = self._get("clients", {"limit": 200})
        items = raw.get("pageItems", []) if isinstance(raw, dict) else raw
        for client in items or []:
            candidates = (
                client.get("mobileNo"),
                client.get("emailAddress"),
                client.get("externalId"),
            )
            if any(self._normalize_identifier(value) == normalized for value in candidates if value):
                client_id = client.get("id")
                return str(client_id) if client_id is not None else None
        return None

    @staticmethod
    def _normalize_identifier(value: Any) -> str:
        text = str(value or "").strip().lower()
        if "@" in text:
            return text
        return re.sub(r"[^0-9]", "", text)

    def _account_id(self, account_id: str) -> int:
        try:
            return int(account_id)
        except ValueError as exc:
            raise ResourceNotFoundError("Account not found") from exc

    def _loan_id(self, loan_id: str) -> int:
        try:
            return int(loan_id)
        except ValueError as exc:
            raise ResourceNotFoundError("Loan not found") from exc

    def get_customer(self, customer_id: str, context: AuthContext) -> Customer:
        client_id = self._customer_id(customer_id, context)
        raw = self._get(f"clients/{client_id}", context=context)
        return Customer(
            id=str(raw.get("id", client_id)),
            displayName=str(raw.get("displayName") or raw.get("firstname", "")),
            status=self._value(raw.get("status"), "active"),
            maskedDocument=self._mask(raw.get("externalId") or raw.get("accountNo")),
            maskedMobile=self._mask(raw.get("mobileNo")),
        )

    def list_accounts(self, customer_id: str, context: AuthContext) -> list[Account]:
        client_id = self._customer_id(customer_id, context)
        raw = self._get(f"clients/{client_id}/accounts", context=context)
        items = raw.get("savingsAccounts", []) if isinstance(raw, dict) else raw
        return [self._account(item) for item in items or []]

    def list_transactions(
        self,
        customer_id: str,
        context: AuthContext,
        limit: int,
        from_date: datetime | None,
        to_date: datetime | None,
    ) -> list[Transaction]:
        client_id = self._customer_id(customer_id, context)
        raw = self._get(f"clients/{client_id}/transactions", {"limit": min(limit, 10)}, context)
        items = raw.get("pageItems", []) if isinstance(raw, dict) else raw
        transactions = [self._transaction(item) for item in items or []]
        if from_date:
            transactions = [item for item in transactions if item.posted_at >= from_date]
        if to_date:
            transactions = [item for item in transactions if item.posted_at <= to_date]
        return transactions[:limit]

    def list_loans(self, customer_id: str, context: AuthContext, status: str | None) -> list[Loan]:
        client_id = self._customer_id(customer_id, context)
        raw = self._get(f"clients/{client_id}/loans", context=context)
        items = raw.get("pageItems", []) if isinstance(raw, dict) else raw
        loans = [self._loan(item) for item in items or []]
        return [item for item in loans if not status or item.status == status]

    def get_account(self, account_id: str, context: AuthContext) -> Account:
        raw = self._get(f"savingsaccounts/{self._account_id(account_id)}", context=context)
        if context.subject != "demo-operator" and str(raw.get("clientId")) != context.subject:
            raise PermissionError("The session is not authorized for this account")
        return self._account(raw)

    def list_cards(self, customer_id: str, context: AuthContext) -> list[Card]:
        self._customer_id(customer_id, context)
        return []

    def list_certificates(self, customer_id: str, context: AuthContext) -> list[Certificate]:
        self._customer_id(customer_id, context)
        return []

    def list_beneficiaries(self, customer_id: str, context: AuthContext) -> list[Beneficiary]:
        self._customer_id(customer_id, context)
        return []

    def list_card_transactions(
        self,
        card_id: str,
        context: AuthContext,
        limit: int,
        from_date: datetime | None,
        to_date: datetime | None,
    ) -> list[Transaction]:
        raise ResourceNotFoundError("Cards are not provided by this Fineract connector")

    def get_loan(self, loan_id: str, context: AuthContext) -> Loan:
        raw = self._get(
            f"loans/{self._loan_id(loan_id)}",
            {"associations": "repaymentSchedule"},
            context,
        )
        if context.subject != "demo-operator" and str(raw.get("clientId")) != context.subject:
            raise PermissionError("The session is not authorized for this loan")
        return self._loan(raw)

    def get_loan_schedule(self, loan_id: str, context: AuthContext) -> list[Installment]:
        loan = self.get_loan(loan_id, context)
        raw = self._get(f"loans/{loan.id}", {"associations": "repaymentSchedule"}, context)
        return self._schedule(raw)

    def list_loan_products(self) -> list[Product]:
        raw = self._get("loanproducts")
        return [self._product(item, "loan") for item in raw or []]

    def list_savings_products(self) -> list[Product]:
        raw = self._get("savingsproducts")
        return [self._product(item, "savings") for item in raw or []]

    def list_billers(self) -> list[Biller]:
        return []

    def get_operation(self, operation_id: str, context: AuthContext) -> OperationStatus:
        raise ResourceNotFoundError("Operation status is not provided by the Fineract demo connector")

    def create_customer_application(
        self, application: CustomerApplication, context: AuthContext
    ) -> ApplicationCreated:
        raise ConnectorError("Customer applications are disabled for the Fineract demo connector")

    def create_loan_application(
        self, application: LoanApplication, context: AuthContext
    ) -> ApplicationCreated:
        raise ConnectorError("Loan applications are disabled for the Fineract demo connector")

    def create_transfer(self, transfer: TransferRequest, context: AuthContext) -> ApplicationCreated:
        raise ConnectorError("Transfers are disabled for the Fineract demo connector")

    def create_payment(self, payment: PaymentRequest, context: AuthContext) -> ApplicationCreated:
        raise ConnectorError("Payments are disabled for the Fineract demo connector")

    def list_locations(
        self,
        latitude: float | None,
        longitude: float | None,
        location_type: str | None,
        limit: int,
    ) -> list[Location]:
        return []

    def create_support_request(
        self, request: SupportRequest, context: AuthContext
    ) -> ApplicationCreated:
        raise ConnectorError("Support requests are disabled for the Fineract demo connector")

    def _account(self, raw: dict[str, Any]) -> Account:
        currency = self._currency(raw)
        account_type = self._value(raw.get("accountType"), "savings").lower()
        account_kind = "checking" if "current" in account_type or "checking" in account_type else "savings"
        return Account(
            id=str(raw.get("id")),
            maskedNumber=self._mask(raw.get("accountNo")),
            type=account_kind,
            name=raw.get("productName"),
            status=self._value(raw.get("status"), "active").lower(),
            availableBalance=Money(amount=self._decimal(raw.get("availableBalance", raw.get("accountBalance", 0))), currency=currency),
            currentBalance=Money(amount=self._decimal(raw.get("accountBalance", 0)), currency=currency),
            updatedAt=datetime.now(timezone.utc),
        )

    def _transaction(self, raw: dict[str, Any]) -> Transaction:
        transaction_type = self._value(raw.get("transactionType"), "transaction").lower()
        direction = "credit" if any(word in transaction_type for word in ("deposit", "credit", "transfer in")) else "debit"
        return Transaction(
            id=str(raw.get("id")),
            postedAt=self._datetime(raw.get("transactionDate")),
            description=str(raw.get("submittedOnDate") or self._value(raw.get("transactionType"), "Transaction")),
            amount=Money(amount=abs(self._decimal(raw.get("amount", 0))), currency=self._currency(raw)),
            direction=direction,
            status="reversed" if raw.get("reversed") else "posted",
            maskedAccountNumber=self._mask(raw.get("accountNo")),
        )

    def _loan(self, raw: dict[str, Any]) -> Loan:
        summary = raw.get("summary") or {}
        principal = self._decimal(raw.get("principal", summary.get("principalDisbursed", 0)))
        outstanding = self._decimal(
            raw.get("loanBalance", summary.get("totalOutstanding", raw.get("principalOutstanding", 0)))
        )
        schedule = self._schedule(raw)
        return Loan(
            id=str(raw.get("id")),
            productName=raw.get("loanProductName"),
            status=self._value(raw.get("status"), "active").lower(),
            principal=Money(amount=principal, currency=self._currency(raw)),
            outstandingBalance=Money(amount=outstanding, currency=self._currency(raw)),
            nextPayment=next((item for item in schedule if item.status != "paid"), None),
            maturityDate=self._date(raw.get("timeline", {}).get("expectedMaturityDate")),
        )

    def _schedule(self, raw: dict[str, Any]) -> list[Installment]:
        schedule = raw.get("repaymentSchedule") or {}
        periods = schedule.get("periods", [])
        result: list[Installment] = []
        for index, item in enumerate(periods, start=1):
            due = self._date(item.get("dueDate"))
            if due is None:
                continue
            paid = bool(item.get("complete") or item.get("completed"))
            total = self._decimal(item.get("totalDue", item.get("totalInstallment", 0)))
            result.append(
                Installment(
                    number=index,
                    dueDate=due,
                    principalDue=Money(amount=self._decimal(item.get("principalDue", 0)), currency=self._currency(raw)),
                    interestDue=Money(amount=self._decimal(item.get("interestDue", 0)), currency=self._currency(raw)),
                    feesDue=Money(amount=self._decimal(item.get("feeChargesDue", 0)), currency=self._currency(raw)),
                    totalDue=Money(amount=total, currency=self._currency(raw)),
                    status="paid" if paid else ("overdue" if due < date.today() else "upcoming"),
                )
            )
        return result

    def _product(self, raw: dict[str, Any], product_type: str) -> Product:
        return Product(
            id=str(raw.get("id")),
            name=str(raw.get("name", "")),
            description=raw.get("description"),
            type=product_type,
            active=bool(raw.get("active", True)),
            currency=self._currency(raw),
        )

    @staticmethod
    def _value(value: Any, default: str) -> str:
        if isinstance(value, dict):
            return str(value.get("value") or value.get("code") or default)
        return str(value or default)

    @staticmethod
    def _decimal(value: Any) -> Decimal:
        try:
            parsed = Decimal(str(value if value is not None else 0))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ConnectorError("The institution core returned an invalid monetary value") from exc
        if not parsed.is_finite():
            raise ConnectorError("The institution core returned a non-finite monetary value")
        return parsed

    @classmethod
    def _currency(cls, raw: dict[str, Any]) -> str:
        currency = raw.get("currency") or raw.get("currencyCode") or {}
        return cls._value(currency, "USD")[:3].upper()

    @staticmethod
    def _mask(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value)
        return f"****{text[-4:]}" if text else None

    @classmethod
    def _date(cls, value: Any) -> date | None:
        if isinstance(value, list) and len(value) >= 3:
            try:
                return date(int(value[0]), int(value[1]), int(value[2]))
            except (TypeError, ValueError):
                return None
        if isinstance(value, str):
            try:
                return date.fromisoformat(value[:10])
            except ValueError:
                return None
        return None

    @classmethod
    def _datetime(cls, value: Any) -> datetime:
        parsed_date = cls._date(value)
        if parsed_date is None:
            raise ConnectorError("The institution core returned an invalid transaction date")
        return datetime.combine(parsed_date, datetime.min.time(), tzinfo=timezone.utc)
