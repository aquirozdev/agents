import hashlib
import hmac
from dataclasses import dataclass
from typing import Literal, cast

from fastapi import HTTPException, Request, status

from .ports import AuthContext
from .settings import Settings


SessionState = Literal["PUBLIC", "VERIFIED", "HUMAN_REVIEW"]


@dataclass(frozen=True)
class TokenVerifier:
    settings: Settings

    def verify(self, request: Request) -> AuthContext:
        authorization = request.headers.get("Authorization", "")
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="A bearer token is required",
                headers={"WWW-Authenticate": "Bearer"},
            )

        if self.settings.environment == "production" and self.settings.auth_mode == "static-demo":
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Production requires auth_mode=external-proxy or a custom TokenVerifier",
            )

        if self.settings.auth_mode in {"static-demo", "dify-user"}:
            if token != self.settings.gateway_token:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid bearer token")
            if self.settings.auth_mode == "dify-user":
                subject = request.headers.get(self.settings.dify_identity_header)
                if not subject:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Dify did not provide an external end-user identity",
                    )
                if self.settings.environment == "production" and not self.settings.dify_identity_secret:
                    raise HTTPException(
                        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        detail="Production dify-user mode requires a Dify identity signing secret",
                    )
                if self.settings.dify_identity_secret:
                    signature = request.headers.get(self.settings.dify_identity_signature_header, "")
                    expected = "sha256=" + hmac.new(
                        self.settings.dify_identity_secret.encode("utf-8"),
                        subject.encode("utf-8"),
                        hashlib.sha256,
                    ).hexdigest()
                    if not hmac.compare_digest(expected, signature):
                        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid identity signature")
            else:
                subject = request.headers.get("X-Demo-Subject", self.settings.demo_subject)
        else:
            # The edge proxy must validate the JWT and strip/overwrite this
            # header. Never expose this mode directly to the public internet.
            if token != self.settings.gateway_token:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid proxy bearer token")
            subject = request.headers.get(self.settings.verified_subject_header)
            if not subject:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="The trusted identity proxy did not provide a verified subject",
                )

        raw_session_state = (
            request.headers.get(self.settings.verified_session_state_header, "PUBLIC").upper()
            if self.settings.auth_mode == "external-proxy"
            else "PUBLIC"
        )
        if raw_session_state not in {"PUBLIC", "VERIFIED", "HUMAN_REVIEW"}:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session state")
        session_state = cast(SessionState, raw_session_state)

        request.state.audit_subject = subject
        request.state.session_state = session_state
        return AuthContext(
            subject=subject,
            institution_id=self.settings.institution_id,
            session_id=request.headers.get("X-Session-ID"),
            session_state=session_state,
            correlation_id=getattr(request.state, "correlation_id", None),
            idempotency_key=request.headers.get("Idempotency-Key"),
        )

    def verify_private(self, request: Request) -> AuthContext:
        """Require an institution-verified session for private data."""

        context = self.verify(request)
        local_demo = self.settings.environment == "development" and self.settings.auth_mode == "static-demo"
        if not local_demo and self.settings.auth_mode != "external-proxy":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Private banking data requires the institution identity proxy",
            )
        if (
            self.settings.require_verified_session_for_private_data
            and not local_demo
            and context.session_state != "VERIFIED"
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="A verified institution session is required for private banking data",
            )
        return context
