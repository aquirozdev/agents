from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings shared by the demo gateway and real connectors.

    The demo defaults are intentionally local-only. A production deployment
    must use an external identity-aware proxy or replace ``TokenVerifier``.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="BANKING_",
        case_sensitive=False,
        extra="ignore",
    )

    environment: Literal["development", "production"] = "development"
    institution_id: str = "cooperative-demo"
    institution_name: str = "Cooperativa Demo"
    institution_locale: str = "es-EC"
    institution_timezone: str = "America/Guayaquil"
    institution_currency: str = "USD"
    institution_config: str | None = None
    connector: Literal["demo", "fineract", "canonical-http", "custom"] = "demo"
    connector_factory: str | None = None
    auth_mode: Literal["static-demo", "dify-user", "external-proxy"] = "static-demo"
    gateway_token: str = "local-demo-token"
    public_gateway_token: str = "local-public-token"
    demo_subject: str = "customer-demo-001"
    verified_subject_header: str = "X-Verified-Subject"
    verified_session_state_header: str = "X-Verified-Session-State"
    require_verified_session_for_private_data: bool = True
    dify_identity_header: str = "X-Dify-End-User-ID"
    dify_identity_signature_header: str = "X-Dify-End-User-Signature"
    dify_identity_secret: str | None = None
    api_version: str = "0.1.0"
    log_level: str = "INFO"
    whatsapp_enabled: bool = False
    whatsapp_verify_token: str | None = None
    whatsapp_app_secret: str | None = None
    whatsapp_identity_secret: str | None = None
    whatsapp_access_token: str | None = None
    whatsapp_phone_number_id: str | None = None
    whatsapp_graph_api_version: str = "v23.0"
    whatsapp_state_db: str = "/data/whatsapp.sqlite3"
    auth_state_db: str = "/data/auth.sqlite3"
    auth_base_url: str = "http://localhost:8080"
    auth_challenge_ttl_seconds: int = 300
    auth_session_ttl_seconds: int = 900
    auth_step_up_ttl_seconds: int = 300
    auth_verification_mode: Literal["local-acceptance", "institution"] = "local-acceptance"
    auth_identity_secret: str = "local-auth-identity-secret"
    auth_customer_directory: str = ""
    dify_base_url: str | None = None
    dify_api_key: str | None = None
    dify_public_api_key: str | None = None
    dify_customer_api_key: str | None = None
    dify_application_user_prefix: str = "whatsapp"
    fineract_base_url: str = "https://fineract:8443/fineract-provider/api/v1"
    fineract_username: str = ""
    fineract_password: str = ""
    fineract_tenant: str = "default"
    fineract_verify_tls: bool = True
    upstream_base_url: str | None = None
    upstream_token: str | None = None
    upstream_verify_tls: bool = True
    upstream_timeout_seconds: float = 15.0
    upstream_connect_timeout_seconds: float = 5.0


def validate_runtime_settings(settings: Settings) -> None:
    """Reject unsafe combinations before the gateway starts serving traffic."""

    if settings.environment != "production":
        return

    errors: list[str] = []
    if settings.connector == "demo":
        errors.append("BANKING_CONNECTOR=demo is not allowed in production")
    if settings.auth_mode == "static-demo":
        errors.append("BANKING_AUTH_MODE=static-demo is not allowed in production")
    if settings.auth_mode == "dify-user" and not settings.dify_identity_secret:
        errors.append("BANKING_DIFY_IDENTITY_SECRET is required for production dify-user mode")
    if not settings.require_verified_session_for_private_data:
        errors.append("private banking data must require a verified session in production")
    identity_headers = (
        settings.verified_subject_header,
        settings.verified_session_state_header,
        settings.dify_identity_header,
        settings.dify_identity_signature_header,
    )
    if any(not header.strip() or any(character in header for character in "\r\n") for header in identity_headers):
        errors.append("verified identity header names must be valid in production")
    if not settings.institution_config:
        errors.append("BANKING_INSTITUTION_CONFIG is required in production")
    if settings.gateway_token == "local-demo-token":
        errors.append("BANKING_GATEWAY_TOKEN must be replaced in production")
    if settings.public_gateway_token == "local-public-token":
        errors.append("BANKING_PUBLIC_GATEWAY_TOKEN must be replaced in production")
    if settings.auth_verification_mode == "local-acceptance":
        errors.append("BANKING_AUTH_VERIFICATION_MODE=local-acceptance is not allowed in production")
    if settings.auth_identity_secret == "local-auth-identity-secret":
        errors.append("BANKING_AUTH_IDENTITY_SECRET must be replaced in production")
    if settings.auth_challenge_ttl_seconds <= 0 or settings.auth_session_ttl_seconds <= 0:
        errors.append("authentication TTLs must be positive")
    if settings.connector == "custom" and not settings.connector_factory:
        errors.append("BANKING_CONNECTOR_FACTORY is required for a production custom connector")
    if settings.connector == "canonical-http":
        if not settings.upstream_base_url:
            errors.append("BANKING_UPSTREAM_BASE_URL is required for canonical-http")
        if not settings.upstream_token:
            errors.append("BANKING_UPSTREAM_TOKEN is required for canonical-http")
    dify_keys = (settings.dify_public_api_key, settings.dify_customer_api_key, settings.dify_api_key)
    if settings.whatsapp_enabled and settings.dify_base_url and not any(dify_keys):
        errors.append("At least one Dify API key is required when WhatsApp is enabled")
    if errors:
        raise RuntimeError("Unsafe production configuration: " + "; ".join(errors))


@lru_cache
def get_settings() -> Settings:
    return Settings()
