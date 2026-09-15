import base64
import hashlib
import hmac
import time
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi import HTTPException
from cryptography.hazmat.primitives.asymmetric import rsa

from identity_proxy.auth import DifyRelayValidator, OIDCValidator
from identity_proxy.settings import Settings, validate_settings


def test_production_settings_require_oidc_and_gateway_secrets() -> None:
    with pytest.raises(RuntimeError, match="Unsafe identity proxy configuration"):
        validate_settings(Settings(environment="production"))


def test_forwarded_headers_are_overwritten_by_proxy_policy() -> None:
    settings = Settings(
        jwks_url="https://issuer.example/jwks",
        gateway_token="gateway-secret",
        institution_id="cooperative-demo",
    )
    import identity_proxy.main as module
    module.settings = settings

    request = SimpleNamespace(
        headers={
            "Authorization": "Bearer user-token",
            "X-Verified-Subject": "attacker",
            "X-Verified-Session-State": "VERIFIED",
            "X-Institution-ID": "attacker-institution",
            "X-Correlation-ID": "corr-1",
            "Cookie": "session=attacker-cookie",
            "X-Forwarded-For": "198.51.100.10",
        }
    )
    headers = module._forward_headers(
        request,
        SimpleNamespace(subject="verified-subject", session_state="PUBLIC", session_id="session-1"),
        "corr-1",
    )
    assert headers["Authorization"] == "Bearer gateway-secret"
    assert headers["X-Verified-Subject"] == "verified-subject"
    assert headers["X-Verified-Session-State"] == "PUBLIC"
    assert headers["X-Institution-ID"] == "cooperative-demo"
    assert "Cookie" not in headers
    assert "X-Forwarded-For" not in headers


def test_validator_rejects_missing_bearer() -> None:
    validator = OIDCValidator.__new__(OIDCValidator)
    validator.settings = Settings()
    with pytest.raises(HTTPException) as error:
        validator.verify(None)
    assert error.value.status_code == 401


def test_validator_accepts_signed_mfa_token_and_derives_verified_session() -> None:
    settings = Settings(
        issuer="https://issuer.example",
        jwks_url="https://issuer.example/jwks",
        audience="banking-agent",
        verified_assurance_values=["mfa"],
    )
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = jwt.encode(
        {
            "iss": settings.issuer,
            "aud": settings.audience,
            "sub": "external-user-001",
            "sid": "session-001",
            "acr": "mfa",
            "iat": datetime.now(timezone.utc),
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        },
        private_key,
        algorithm="RS256",
    )
    validator = OIDCValidator.__new__(OIDCValidator)
    validator.settings = settings
    validator.jwks_client = SimpleNamespace(
        get_signing_key_from_jwt=lambda _: SimpleNamespace(key=private_key.public_key())
    )

    identity = validator.verify(f"Bearer {token}")
    assert identity.subject == "external-user-001"
    assert identity.session_id == "session-001"
    assert identity.session_state == "VERIFIED"


def test_dify_relay_requires_both_dify_and_channel_signatures_for_verified_state() -> None:
    settings = Settings(dify_relay_secret="dify-secret", identity_assertion_secret="channel-secret")
    subject = "core-customer-001"
    expires_at = int(time.time()) + 300
    encoded_subject = base64.urlsafe_b64encode(subject.encode()).decode().rstrip("=")
    channel_signature = hmac.new(
        b"channel-secret", f"{subject}|{expires_at}".encode(), hashlib.sha256
    ).hexdigest()
    identity = f"banking:v1:{encoded_subject}:{expires_at}:{channel_signature}"
    dify_signature = "sha256=" + hmac.new(b"dify-secret", identity.encode(), hashlib.sha256).hexdigest()

    validator = DifyRelayValidator(settings)
    verified = validator.verify(identity, dify_signature)
    assert verified.subject == subject
    assert verified.session_state == "VERIFIED"

    public = validator.verify("whatsapp:sender-001", "sha256=" + hmac.new(
        b"dify-secret", b"whatsapp:sender-001", hashlib.sha256
    ).hexdigest())
    assert public.session_state == "PUBLIC"
