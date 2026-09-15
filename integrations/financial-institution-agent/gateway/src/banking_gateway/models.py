from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ContractModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, str_strip_whitespace=True)


class Health(ContractModel):
    status: Literal["ok", "degraded"]
    version: str
    correlation_id: str | None = Field(default=None, alias="correlationId")


class Capabilities(ContractModel):
    customer_profile: bool = True
    accounts: bool = True
    account_transactions: bool = True
    loans: bool = True
    savings_products: bool = True
    loan_products: bool = True
    loan_applications: bool = True
    loan_schedules: bool = True
    locations: bool = True
    support_requests: bool = True
    customer_applications: bool = True
    cards: bool = False
    beneficiaries: bool = False
    billers: bool = False
    operation_status: bool = False
    certificates: bool = False
    payments: bool = False
    transfers: bool = False


class Money(ContractModel):
    amount: Decimal
    currency: str = Field(min_length=3, max_length=3)


class Customer(ContractModel):
    id: str
    display_name: str = Field(alias="displayName")
    status: str
    masked_document: str | None = Field(default=None, alias="maskedDocument")
    masked_mobile: str | None = Field(default=None, alias="maskedMobile")
    branch_name: str | None = Field(default=None, alias="branchName")


AccountType = Literal["savings", "checking", "fixed_deposit", "other"]


class Account(ContractModel):
    id: str
    masked_number: str | None = Field(default=None, alias="maskedNumber")
    type: AccountType
    name: str | None = None
    status: str
    available_balance: Money = Field(alias="availableBalance")
    current_balance: Money | None = Field(default=None, alias="currentBalance")
    updated_at: datetime | None = Field(default=None, alias="updatedAt")


class Card(ContractModel):
    id: str
    masked_number: str = Field(alias="maskedNumber")
    type: Literal["debit", "credit", "other"]
    name: str | None = None
    status: str
    available_balance: Money | None = Field(default=None, alias="availableBalance")
    current_balance: Money | None = Field(default=None, alias="currentBalance")
    expiry_month: int | None = Field(default=None, alias="expiryMonth", ge=1, le=12)
    expiry_year: int | None = Field(default=None, alias="expiryYear", ge=2000, le=2200)


class Certificate(ContractModel):
    id: str
    type: Literal["account_ownership", "balance", "tax", "other"]
    name: str
    status: Literal["available", "processing", "expired"]
    issued_at: datetime | None = Field(default=None, alias="issuedAt")
    download_url: str | None = Field(default=None, alias="downloadUrl")


class Beneficiary(ContractModel):
    id: str
    name: str
    type: str
    masked_account: str | None = Field(default=None, alias="maskedAccount")
    status: str


class Biller(ContractModel):
    id: str
    name: str
    category: str
    status: str


class Transaction(ContractModel):
    id: str
    posted_at: datetime = Field(alias="postedAt")
    description: str
    amount: Money
    direction: Literal["credit", "debit"]
    status: Literal["posted", "pending", "reversed"]
    masked_account_number: str | None = Field(default=None, alias="maskedAccountNumber")


class Installment(ContractModel):
    number: int | None = Field(default=None, ge=1)
    due_date: date = Field(alias="dueDate")
    principal_due: Money | None = Field(default=None, alias="principalDue")
    interest_due: Money | None = Field(default=None, alias="interestDue")
    fees_due: Money | None = Field(default=None, alias="feesDue")
    total_due: Money = Field(alias="totalDue")
    status: Literal["upcoming", "due", "overdue", "paid"]


class Loan(ContractModel):
    id: str
    product_name: str | None = Field(default=None, alias="productName")
    status: str
    principal: Money
    outstanding_balance: Money = Field(alias="outstandingBalance")
    next_payment: Installment | None = Field(default=None, alias="nextPayment")
    maturity_date: date | None = Field(default=None, alias="maturityDate")


class Product(ContractModel):
    id: str
    name: str
    description: str | None = None
    type: str
    active: bool
    currency: str | None = None


class Consent(ContractModel):
    accepted: Literal[True]
    version: str = Field(min_length=1)


class CustomerApplication(ContractModel):
    full_name: str = Field(alias="fullName", min_length=2, max_length=200)
    contact: str = Field(min_length=3, max_length=100)
    external_reference: str | None = Field(default=None, alias="externalReference", max_length=128)
    consent: Consent
    metadata: dict[str, Any] = Field(default_factory=dict)


class LoanApplication(ContractModel):
    customer_id: str = Field(alias="customerId")
    product_id: str = Field(alias="productId")
    requested_amount: Money = Field(alias="requestedAmount")
    term: int = Field(ge=1, le=600)
    purpose: str | None = Field(default=None, max_length=500)
    consent: Consent
    metadata: dict[str, Any] = Field(default_factory=dict)


class TransferRequest(ContractModel):
    source_account_id: str = Field(alias="sourceAccountId", min_length=1, max_length=128)
    beneficiary_id: str = Field(alias="beneficiaryId", min_length=1, max_length=128)
    amount: Money
    purpose: str | None = Field(default=None, max_length=500)
    consent: Consent
    metadata: dict[str, Any] = Field(default_factory=dict)


class PaymentRequest(ContractModel):
    source_account_id: str = Field(alias="sourceAccountId", min_length=1, max_length=128)
    biller_id: str = Field(alias="billerId", min_length=1, max_length=128)
    amount: Money
    reference: str | None = Field(default=None, max_length=200)
    consent: Consent
    metadata: dict[str, Any] = Field(default_factory=dict)


class Location(ContractModel):
    id: str
    type: Literal["branch", "atm", "agent"]
    name: str
    address: str
    latitude: float | None = None
    longitude: float | None = None
    opening_hours: str | None = Field(default=None, alias="openingHours")


class SupportRequest(ContractModel):
    category: Literal["question", "complaint", "fraud", "technical", "human_handoff"]
    description: str = Field(min_length=1, max_length=2000)
    consent: Literal[True]
    preferred_contact: str | None = Field(default=None, alias="preferredContact", max_length=100)


class ApplicationCreated(ContractModel):
    id: str
    status: Literal["pending", "received", "queued"]
    created_at: datetime = Field(alias="createdAt")
    correlation_id: str | None = Field(default=None, alias="correlationId")


class OperationStatus(ContractModel):
    id: str
    kind: Literal["customer_application", "loan_application", "transfer", "payment", "support"]
    status: Literal["pending", "received", "approved", "rejected", "completed", "failed"]
    detail: str | None = None
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime | None = Field(default=None, alias="updatedAt")
