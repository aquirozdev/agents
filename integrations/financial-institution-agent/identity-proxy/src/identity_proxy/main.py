import uuid
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Request, Response, status
from starlette.concurrency import run_in_threadpool

from .auth import DifyRelayValidator, OIDCValidator
from .settings import Settings, get_settings


settings: Settings = get_settings()
oidc_validator = OIDCValidator(settings) if settings.issuer and settings.jwks_url and settings.audience else None
dify_relay_validator = DifyRelayValidator(settings) if settings.dify_relay_secret else None
HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
    "content-length",
    "content-encoding",
}


@asynccontextmanager
async def lifespan(_: FastAPI):
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0)) as client:
        app.state.client = client
        yield


app = FastAPI(
    title="Financial Institution Identity Proxy",
    version="0.1.0",
    docs_url=None if settings.environment == "production" else "/docs",
    redoc_url=None if settings.environment == "production" else "/redoc",
    openapi_url=None if settings.environment == "production" else "/openapi.json",
    lifespan=lifespan,
)


@app.get("/health", include_in_schema=False)
async def health() -> dict[str, str]:
    return {"status": "ok"}


def _forward_headers(request: Request, identity, correlation_id: str) -> dict[str, str]:
    blocked = {
        "host",
        "authorization",
        settings.verified_subject_header.lower(),
        settings.verified_session_state_header.lower(),
        settings.session_id_header.lower(),
        "x-institution-id",
        settings.dify_identity_header.lower(),
        settings.dify_identity_signature_header.lower(),
        "cookie",
        "forwarded",
        "x-forwarded-for",
        "x-forwarded-host",
        "x-forwarded-proto",
    }
    headers = {key: value for key, value in request.headers.items() if key.lower() not in blocked}
    headers["Authorization"] = f"Bearer {settings.gateway_token}"
    headers[settings.verified_subject_header] = identity.subject
    headers[settings.verified_session_state_header] = identity.session_state
    headers[settings.correlation_id_header] = correlation_id
    headers[settings.session_id_header] = identity.session_id or correlation_id
    if settings.institution_id:
        headers["X-Institution-ID"] = settings.institution_id
    return headers


@app.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    include_in_schema=False,
)
async def proxy(request: Request, path: str) -> Response:
    authorization = request.headers.get("Authorization", "")
    scheme, _, token = authorization.partition(" ")
    if (
        scheme.lower() == "bearer"
        and token == settings.gateway_token
        and dify_relay_validator is not None
    ):
        identity = dify_relay_validator.verify(
            request.headers.get(settings.dify_identity_header),
            request.headers.get(settings.dify_identity_signature_header),
        )
    elif oidc_validator is not None:
        # PyJWT's JWKS client performs cache misses synchronously. Keep
        # issuer/JWKS retrieval off FastAPI's event loop while still validating.
        identity = await run_in_threadpool(oidc_validator.verify, authorization)
    else:
        raise HTTPException(status_code=503, detail="Identity proxy is not configured")
    body = await request.body()
    if len(body) > settings.max_body_bytes:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Request body is too large")
    requested_correlation_id = request.headers.get(settings.correlation_id_header)
    correlation_id = (
        requested_correlation_id
        if requested_correlation_id
        and len(requested_correlation_id) <= 128
        and not any(character in requested_correlation_id for character in "\r\n")
        else uuid.uuid4().hex
    )
    upstream_url = f"{settings.gateway_base_url.rstrip('/')}/{path}"
    try:
        response = await request.app.state.client.request(
            request.method,
            upstream_url,
            params=request.query_params,
            content=body,
            headers=_forward_headers(request, identity, correlation_id),
        )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="The banking gateway is unavailable") from exc
    response_headers = {
        key: value for key, value in response.headers.items() if key.lower() not in HOP_BY_HOP_HEADERS
    }
    response_headers[settings.correlation_id_header] = response.headers.get(
        settings.correlation_id_header, correlation_id
    )
    return Response(
        content=response.content,
        status_code=response.status_code,
        headers=response_headers,
        media_type=response.headers.get("content-type"),
    )
