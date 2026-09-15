from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="IDENTITY_PROXY_",
        case_sensitive=False,
        extra="ignore",
    )

    environment: str = "development"
    issuer: str = ""
    jwks_url: str = ""
    audience: str = ""
    algorithms: list[str] = ["RS256"]
    subject_claim: str = "sub"
    session_id_claim: str = "sid"
    assurance_claim: str = "acr"
    verified_assurance_values: list[str] = ["mfa", "verified", "urn:institution:mfa"]
    gateway_base_url: str = "http://banking-gateway:8080"
    gateway_token: str = ""
    institution_id: str = ""
    dify_relay_secret: str = ""
    identity_assertion_secret: str = ""
    dify_identity_header: str = "X-Dify-End-User-ID"
    dify_identity_signature_header: str = "X-Dify-End-User-Signature"
    verified_subject_header: str = "X-Verified-Subject"
    verified_session_state_header: str = "X-Verified-Session-State"
    session_id_header: str = "X-Session-ID"
    correlation_id_header: str = "X-Correlation-ID"
    max_body_bytes: int = 1_048_576
    jwks_cache_seconds: int = 300


def validate_settings(settings: Settings) -> None:
    if settings.environment != "production":
        return
    errors: list[str] = []
    oidc_configured = bool(settings.issuer and settings.jwks_url and settings.audience)
    relay_configured = bool(settings.dify_relay_secret)
    if not oidc_configured and not relay_configured:
        errors.append("OIDC settings or IDENTITY_PROXY_DIFY_RELAY_SECRET are required in production")
    if not settings.gateway_token or settings.gateway_token == "local-demo-token":
        errors.append("IDENTITY_PROXY_GATEWAY_TOKEN must be a secret in production")
    if not settings.institution_id:
        errors.append("IDENTITY_PROXY_INSTITUTION_ID is required in production")
    if not settings.algorithms or any(algorithm not in {"RS256", "RS384", "RS512", "ES256", "ES384", "ES512"} for algorithm in settings.algorithms):
        errors.append("IDENTITY_PROXY_ALGORITHMS contains an unsupported algorithm")
    headers = (
        settings.verified_subject_header,
        settings.verified_session_state_header,
        settings.session_id_header,
        settings.correlation_id_header,
        settings.dify_identity_header,
        settings.dify_identity_signature_header,
    )
    if any(not header.strip() or any(character in header for character in "\r\n") for header in headers):
        errors.append("identity proxy header names must be valid")
    if settings.max_body_bytes <= 0:
        errors.append("IDENTITY_PROXY_MAX_BODY_BYTES must be positive")
    if errors:
        raise RuntimeError("Unsafe identity proxy configuration: " + "; ".join(errors))


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    validate_settings(settings)
    return settings
