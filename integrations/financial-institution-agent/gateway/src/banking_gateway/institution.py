from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .models import Capabilities
from .settings import Settings


@dataclass(frozen=True)
class InstitutionProfile:
    id: str
    legal_name: str
    locale: str
    timezone: str
    currency: str
    capabilities: Capabilities
    require_verified_session_for_private_data: bool = True
    verified_session_state_header: str = "X-Verified-Session-State"


def load_institution_profile(settings: Settings) -> InstitutionProfile:
    """Load optional institution policy without coupling it to a core."""

    if not settings.institution_config:
        return InstitutionProfile(
            id=settings.institution_id,
            legal_name=settings.institution_name,
            locale=settings.institution_locale,
            timezone=settings.institution_timezone,
            currency=settings.institution_currency,
            # The local demo exposes the common read-only and pending-request
            # surfaces plus cards so the commercial conversation can be
            # exercised without an institution policy file. A real deployment
            # must provide institution.yaml; its explicit capability matrix
            # remains the source of truth.
            capabilities=Capabilities(cards=True),
            require_verified_session_for_private_data=settings.require_verified_session_for_private_data,
            verified_session_state_header=settings.verified_session_state_header,
        )

    path = Path(settings.institution_config)
    if not path.is_file():
        raise RuntimeError(f"Institution configuration does not exist: {path}")
    document: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    institution = document.get("institution") or {}
    gateway = document.get("banking_gateway") or {}
    configured_capabilities = document.get("capabilities") or {}
    security = document.get("security") or {}
    if not institution.get("id") or not institution.get("legal_name"):
        raise RuntimeError("Institution configuration requires institution.id and institution.legal_name")
    if gateway.get("base_url") and not isinstance(gateway["base_url"], str):
        raise RuntimeError("Institution banking_gateway.base_url must be a string")

    safe_capabilities = {
        name: bool(configured_capabilities.get(name, False)) for name in Capabilities.model_fields
    }
    return InstitutionProfile(
        id=str(institution["id"]),
        legal_name=str(institution["legal_name"]),
        locale=str(institution.get("locale", settings.institution_locale)),
        timezone=str(institution.get("timezone", settings.institution_timezone)),
        currency=str(institution.get("base_currency", settings.institution_currency)),
        capabilities=Capabilities(**safe_capabilities),
        require_verified_session_for_private_data=bool(
            security.get("require_verified_session_for_private_data", True)
        ),
        verified_session_state_header=str(
            security.get("verified_session_state_header", settings.verified_session_state_header)
        ),
    )
