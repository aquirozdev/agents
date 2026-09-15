import hashlib
import json
import logging
from typing import Any


logger = logging.getLogger("banking_gateway.audit")


def record_request(
    *,
    correlation_id: str,
    method: str,
    path: str,
    status_code: int,
    subject: str | None = None,
    session_state: str | None = None,
) -> None:
    """Emit a structured, non-sensitive audit event for SIEM ingestion."""

    event: dict[str, Any] = {
        "event": "banking_gateway_request",
        "correlationId": correlation_id,
        "method": method,
        "path": path,
        "status": status_code,
    }
    if subject:
        event["subjectHash"] = hashlib.sha256(subject.encode("utf-8")).hexdigest()[:16]
    if session_state:
        event["sessionState"] = session_state
    logger.info(json.dumps(event, separators=(",", ":"), sort_keys=True))
