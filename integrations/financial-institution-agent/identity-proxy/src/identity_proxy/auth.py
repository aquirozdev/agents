import base64
import hashlib
import hmac
import time
from dataclasses import dataclass
from typing import Any

import jwt
from fastapi import HTTPException, status

from .settings import Settings


@dataclass(frozen=True)
class VerifiedIdentity:
    subject: str
    session_id: str | None
    session_state: str


class OIDCValidator:
    """Validate an institution-issued JWT before forwarding any request."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.jwks_client = jwt.PyJWKClient(
            settings.jwks_url,
            cache_jwk_set=True,
            lifespan=settings.jwks_cache_seconds,
        )

    def verify(self, authorization: str | None) -> VerifiedIdentity:
        scheme, _, token = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="A bearer token is required",
                headers={"WWW-Authenticate": "Bearer"},
            )
        try:
            signing_key = self.jwks_client.get_signing_key_from_jwt(token).key
            claims: dict[str, Any] = jwt.decode(
                token,
                signing_key,
                algorithms=self.settings.algorithms,
                audience=self.settings.audience,
                issuer=self.settings.issuer,
                options={"require": ["exp", "iat", self.settings.subject_claim]},
            )
        except (jwt.PyJWTError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="The institution identity token is invalid or expired",
                headers={"WWW-Authenticate": "Bearer"},
            ) from exc

        subject = claims.get(self.settings.subject_claim)
        if not isinstance(subject, str) or not subject.strip() or any(character in subject for character in "\r\n"):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="The identity token has no valid subject")

        assurance = claims.get(self.settings.assurance_claim)
        values = assurance if isinstance(assurance, list) else [assurance]
        verified = any(str(value) in self.settings.verified_assurance_values for value in values if value is not None)
        session_id = claims.get(self.settings.session_id_claim) or claims.get("jti")
        if session_id is not None and not isinstance(session_id, str):
            session_id = str(session_id)
        if session_id is not None and (
            not session_id.strip()
            or len(session_id) > 256
            or any(character in session_id for character in "\r\n")
        ):
            session_id = None
        return VerifiedIdentity(
            subject=subject,
            session_id=session_id,
            session_state="VERIFIED" if verified else "PUBLIC",
        )


class DifyRelayValidator:
    """Validate Dify's signed end-user relay and an optional channel assertion."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def verify(self, identity: str | None, signature: str | None) -> VerifiedIdentity:
        if not identity or not signature or not self.settings.dify_relay_secret:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Dify identity relay is missing")
        expected = "sha256=" + hmac.new(
            self.settings.dify_relay_secret.encode("utf-8"),
            identity.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Dify identity signature")
        if any(character in identity for character in "\r\n") or len(identity) > 255:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Dify identity")

        if not identity.startswith("banking:v1:"):
            return VerifiedIdentity(subject=identity, session_id=None, session_state="PUBLIC")

        parts = identity.split(":")
        if len(parts) != 5:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid channel identity assertion")
        _, version, encoded_subject, raw_expires_at, assertion_signature = parts
        if version != "v1" or not self.settings.identity_assertion_secret:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid channel identity assertion")
        try:
            expires_at = int(raw_expires_at)
            padded = encoded_subject + "=" * (-len(encoded_subject) % 4)
            subject = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
        except (ValueError, UnicodeDecodeError, base64.binascii.Error) as exc:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid channel identity assertion") from exc
        if expires_at <= int(time.time()) or not subject or any(character in subject for character in "\r\n"):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Expired channel identity assertion")
        expected_assertion = hmac.new(
            self.settings.identity_assertion_secret.encode("utf-8"),
            f"{subject}|{expires_at}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected_assertion, assertion_signature):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid channel identity assertion")
        return VerifiedIdentity(subject=subject, session_id=f"dify:{assertion_signature[:16]}", session_state="VERIFIED")
