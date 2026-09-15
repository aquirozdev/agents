import sqlite3

import pytest
from fastapi import HTTPException

from banking_gateway.auth_flow import AuthFlow
from banking_gateway.settings import Settings


class DirectoryConnector:
    def resolve_customer_identifier(self, identifier: str) -> str | None:
        return {
            "593999292849": "101",
            "593985613152": "102",
        }.get(identifier)


def make_flow(tmp_path) -> AuthFlow:
    settings = Settings(
        auth_state_db=str(tmp_path / "auth.sqlite3"),
        whatsapp_state_db=str(tmp_path / "whatsapp.sqlite3"),
        auth_base_url="https://bank.example",
    )
    return AuthFlow(settings, DirectoryConnector())


def test_otp_creates_verified_session_for_the_resolved_customer(tmp_path) -> None:
    flow = make_flow(tmp_path)
    challenge = flow.start_challenge("+593 999 292 849", "web", "sms")

    session = flow.verify_otp(challenge.challenge_id, "123456")
    response = flow.response_for_session(session)

    assert session.subject == "101"
    assert response["sessionState"] == "VERIFIED"
    assert response["identityAssertion"].startswith("banking:v1:")


def test_whatsapp_otp_persists_identity_for_the_channel(tmp_path) -> None:
    flow = make_flow(tmp_path)
    challenge = flow.start_challenge("+593985613152", "whatsapp", "sms")

    flow.verify_otp(challenge.challenge_id, "654321")

    with sqlite3.connect(tmp_path / "whatsapp.sqlite3") as db:
        row = db.execute(
            "SELECT subject, state FROM whatsapp_identities WHERE sender = ?",
            ("593985613152",),
        ).fetchone()
    assert row == ("102", "VERIFIED")


def test_otp_challenge_is_single_use(tmp_path) -> None:
    flow = make_flow(tmp_path)
    challenge = flow.start_challenge("+593999292849", "web", "sms")
    flow.verify_otp(challenge.challenge_id, "123456")

    with pytest.raises(HTTPException) as error:
        flow.verify_otp(challenge.challenge_id, "123456")
    assert error.value.status_code == 401


def test_unknown_customer_cannot_start_authentication(tmp_path) -> None:
    flow = make_flow(tmp_path)

    with pytest.raises(HTTPException) as error:
        flow.start_challenge("+593900000000", "web", "sms")
    assert error.value.status_code == 404


def test_biometric_step_up_exposes_platform_authenticator_options(tmp_path) -> None:
    flow = make_flow(tmp_path)
    challenge = flow.start_challenge("+593999292849", "web", "sms")
    session = flow.verify_otp(challenge.challenge_id, "123456")

    options = flow.start_biometric(session.token)

    assert options["method"] == "platform-biometric"
    assert options["publicKey"]["authenticatorSelection"]["userVerification"] == "required"
