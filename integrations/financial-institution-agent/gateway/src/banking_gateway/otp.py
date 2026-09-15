import re
from typing import Protocol

import httpx

from .settings import Settings


class OtpProviderError(RuntimeError):
    """An OTP provider could not complete the requested operation."""


class OtpProvider(Protocol):
    def send_code(self, recipient: str, delivery: str) -> None: ...

    def check_code(self, recipient: str, delivery: str, code: str) -> bool: ...


class LocalAcceptanceOtpProvider:
    """Deterministic provider used only by the local demo."""

    def send_code(self, recipient: str, delivery: str) -> None:
        return None

    def check_code(self, recipient: str, delivery: str, code: str) -> bool:
        return bool(re.fullmatch(r"\d{6}", code.strip()))


class TwilioVerifyOtpProvider:
    """Twilio Verify v2 adapter for SMS/email possession checks."""

    def __init__(self, settings: Settings) -> None:
        self.account_sid = settings.twilio_account_sid or ""
        self.auth_token = settings.twilio_auth_token or ""
        self.service_sid = settings.twilio_verify_service_sid or ""
        self.base_url = "https://verify.twilio.com/v2/Services"

    @staticmethod
    def _target(recipient: str, delivery: str) -> str:
        if delivery == "sms" and recipient.isdigit():
            return f"+{recipient}"
        return recipient

    def _post(self, path: str, data: dict[str, str]) -> httpx.Response:
        try:
            with httpx.Client(
                auth=(self.account_sid, self.auth_token),
                timeout=10.0,
            ) as client:
                response = client.post(f"{self.base_url}/{self.service_sid}/{path}", data=data)
            response.raise_for_status()
            return response
        except (httpx.HTTPError, ValueError) as exc:
            raise OtpProviderError("Twilio Verify request failed") from exc

    def send_code(self, recipient: str, delivery: str) -> None:
        if delivery not in {"sms", "email"}:
            raise OtpProviderError("Unsupported Twilio Verify delivery channel")
        self._post(
            "Verifications",
            {"To": self._target(recipient, delivery), "Channel": delivery},
        )

    def check_code(self, recipient: str, delivery: str, code: str) -> bool:
        try:
            response = self._post(
                "VerificationCheck",
                {
                    "To": self._target(recipient, delivery),
                    "Code": code.strip(),
                },
            )
        except OtpProviderError as exc:
            cause = exc.__cause__
            if isinstance(cause, httpx.HTTPStatusError) and cause.response.status_code == 404:
                return False
            raise
        try:
            return response.json().get("status") == "approved"
        except (ValueError, AttributeError) as exc:
            raise OtpProviderError("Twilio Verify returned an invalid response") from exc


def build_otp_provider(settings: Settings) -> OtpProvider:
    if settings.auth_otp_provider == "local-acceptance":
        if settings.auth_verification_mode != "local-acceptance":
            raise RuntimeError("Local OTP acceptance cannot be used with institutional verification")
        return LocalAcceptanceOtpProvider()
    if not all(
        (settings.twilio_account_sid, settings.twilio_auth_token, settings.twilio_verify_service_sid)
    ):
        raise RuntimeError(
            "BANKING_TWILIO_ACCOUNT_SID, BANKING_TWILIO_AUTH_TOKEN and "
            "BANKING_TWILIO_VERIFY_SERVICE_SID are required for Twilio Verify"
        )
    return TwilioVerifyOtpProvider(settings)
