import base64
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .settings import Settings
from .otp import OtpProvider, OtpProviderError, build_otp_provider


AuthChannel = Literal["web", "whatsapp"]
DeliveryMethod = Literal["sms", "email"]


@dataclass(frozen=True)
class AuthChallenge:
    challenge_id: str
    subject: str
    recipient: str
    channel: AuthChannel
    delivery: DeliveryMethod
    expires_at: int
    auth_url: str


@dataclass(frozen=True)
class AuthSession:
    token: str
    subject: str
    channel: AuthChannel
    expires_at: int
    step_up_expires_at: int | None


class AuthStartRequest(BaseModel):
    identifier: str = Field(min_length=3, max_length=160)
    channel: AuthChannel = "web"
    delivery: DeliveryMethod = "sms"


class OtpVerificationRequest(BaseModel):
    challenge_id: str = Field(min_length=20, max_length=128, alias="challengeId")
    code: str = Field(min_length=4, max_length=12)


class SessionRequest(BaseModel):
    session_token: str = Field(min_length=20, max_length=256, alias="sessionToken")


class BiometricCompletionRequest(SessionRequest):
    assertion: str = Field(default="local-platform-assertion", min_length=1, max_length=20_000)


class WebChatRequest(SessionRequest):
    message: str = Field(min_length=1, max_length=4_000)
    conversation_id: str | None = Field(default=None, max_length=128, alias="conversationId")


class AuthFlow:
    """Institution identity flow with a replaceable local acceptance provider.

    The local provider deliberately keeps the production contract: a challenge
    is issued, the user verifies it, and a short-lived session is created. In
    local-acceptance mode any six-digit OTP and any non-empty platform assertion
    are accepted. That mode is rejected by production configuration validation.
    """

    def __init__(self, settings: Settings, connector: Any, otp_provider: OtpProvider | None = None) -> None:
        self.settings = settings
        self.connector = connector
        self.otp_provider = otp_provider or build_otp_provider(settings)

    def _connect(self) -> sqlite3.Connection:
        Path(self.settings.auth_state_db).parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.settings.auth_state_db, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout = 10000")
        db.execute("PRAGMA journal_mode = WAL")
        db.execute(
            "CREATE TABLE IF NOT EXISTS auth_challenges ("
            "challenge_id TEXT PRIMARY KEY, recipient TEXT NOT NULL, subject TEXT NOT NULL, "
            "channel TEXT NOT NULL, delivery TEXT NOT NULL, expires_at INTEGER NOT NULL, "
            "used INTEGER NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL)"
        )
        db.execute(
            "CREATE TABLE IF NOT EXISTS auth_sessions ("
            "token_hash TEXT PRIMARY KEY, subject TEXT NOT NULL, channel TEXT NOT NULL, "
            "expires_at INTEGER NOT NULL, step_up_expires_at INTEGER)"
        )
        return db

    @staticmethod
    def normalize_identifier(value: str) -> str:
        text = value.strip().lower()
        if "@" in text:
            return text
        return re.sub(r"[^0-9]", "", text)

    def resolve_subject(self, identifier: str) -> str | None:
        normalized = self.normalize_identifier(identifier)
        resolver = getattr(self.connector, "resolve_customer_identifier", None)
        if callable(resolver):
            return resolver(normalized)

        # Useful for a local connector that does not expose a directory yet.
        # Production deployments must resolve through their institution adapter.
        directory = getattr(self.settings, "auth_customer_directory", "")
        if directory:
            try:
                records = json.loads(directory)
            except json.JSONDecodeError:
                records = []
            for record in records if isinstance(records, list) else []:
                if not isinstance(record, dict) or not record.get("subject"):
                    continue
                values = [record.get("mobile"), record.get("email"), record.get("externalId")]
                if any(self.normalize_identifier(str(value)) == normalized for value in values if value):
                    return str(record["subject"])
        return None

    def start_challenge(
        self,
        identifier: str,
        channel: AuthChannel,
        delivery: DeliveryMethod,
    ) -> AuthChallenge:
        subject = self.resolve_subject(identifier)
        if not subject:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Customer identity was not found")
        challenge_id = secrets.token_urlsafe(32)
        now = int(time.time())
        expires_at = now + self.settings.auth_challenge_ttl_seconds
        recipient = self.normalize_identifier(identifier)
        try:
            self.otp_provider.send_code(recipient, delivery)
        except OtpProviderError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="The verification code could not be sent",
            ) from exc
        with closing(self._connect()) as db:
            db.execute(
                "INSERT INTO auth_challenges "
                "(challenge_id, recipient, subject, channel, delivery, expires_at, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (challenge_id, recipient, subject, channel, delivery, expires_at, now),
            )
            db.commit()
        return AuthChallenge(
            challenge_id=challenge_id,
            subject=subject,
            recipient=recipient,
            channel=channel,
            delivery=delivery,
            expires_at=expires_at,
            auth_url=(
                f"{self.settings.auth_base_url.rstrip('/')}/channels/auth"
                f"?challenge={challenge_id}"
            ),
        )

    def pending_challenge(self, recipient: str, channel: AuthChannel) -> AuthChallenge | None:
        normalized = self.normalize_identifier(recipient)
        now = int(time.time())
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT challenge_id, subject, recipient, channel, delivery, expires_at "
                "FROM auth_challenges WHERE recipient = ? AND channel = ? AND used = 0 "
                "AND expires_at > ? ORDER BY created_at DESC LIMIT 1",
                (normalized, channel, now),
            ).fetchone()
        if not row:
            return None
        return AuthChallenge(
            challenge_id=str(row["challenge_id"]),
            subject=str(row["subject"]),
            recipient=str(row["recipient"]),
            channel=channel,
            delivery=str(row["delivery"]),  # type: ignore[arg-type]
            expires_at=int(row["expires_at"]),
            auth_url=(
                f"{self.settings.auth_base_url.rstrip('/')}/channels/auth"
                f"?challenge={row['challenge_id']}"
            ),
        )

    def verify_otp(self, challenge_id: str, code: str) -> AuthSession:
        if not re.fullmatch(r"\d{6}", code.strip()):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid verification code")
        now = int(time.time())
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT * FROM auth_challenges WHERE challenge_id = ?", (challenge_id,)
            ).fetchone()
            if not row or int(row["used"]) or int(row["expires_at"]) <= now:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="The verification challenge expired")
            attempts = int(row["attempts"]) + 1
            if attempts > 5:
                raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many verification attempts")
            db.execute("UPDATE auth_challenges SET attempts = ? WHERE challenge_id = ?", (attempts, challenge_id))
            db.commit()

        try:
            verified = self.otp_provider.check_code(str(row["recipient"]), str(row["delivery"]), code)
        except OtpProviderError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="The verification service is unavailable",
            ) from exc
        if not verified:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid verification code")

        with closing(self._connect()) as db:
            current = db.execute(
                "SELECT used, expires_at FROM auth_challenges WHERE challenge_id = ?", (challenge_id,)
            ).fetchone()
            if not current or int(current["used"]) or int(current["expires_at"]) <= now:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="The verification challenge expired")
            db.execute("UPDATE auth_challenges SET used = 1 WHERE challenge_id = ?", (challenge_id,))
            token = secrets.token_urlsafe(48)
            token_hash = self._hash_token(token)
            expires_at = now + self.settings.auth_session_ttl_seconds
            db.execute(
                "INSERT INTO auth_sessions(token_hash, subject, channel, expires_at, step_up_expires_at) "
                "VALUES (?, ?, ?, ?, NULL)",
                (token_hash, str(row["subject"]), str(row["channel"]), expires_at),
            )
            db.commit()
        session = AuthSession(token, str(row["subject"]), str(row["channel"]), expires_at, None)  # type: ignore[arg-type]
        if session.channel == "whatsapp":
            self._save_whatsapp_identity(str(row["recipient"]), session.subject, expires_at)
        return session

    def session(self, token: str) -> AuthSession:
        now = int(time.time())
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT subject, channel, expires_at, step_up_expires_at FROM auth_sessions WHERE token_hash = ?",
                (self._hash_token(token),),
            ).fetchone()
        if not row or int(row["expires_at"]) <= now:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="The authentication session expired")
        return AuthSession(
            token=token,
            subject=str(row["subject"]),
            channel=str(row["channel"]),  # type: ignore[arg-type]
            expires_at=int(row["expires_at"]),
            step_up_expires_at=(
                int(row["step_up_expires_at"]) if row["step_up_expires_at"] is not None else None
            ),
        )

    def start_biometric(self, token: str) -> dict[str, Any]:
        session = self.session(token)
        if self.settings.auth_verification_mode != "local-acceptance":
            raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="Institution WebAuthn provider is not configured")
        challenge = secrets.token_bytes(32)
        user_id = base64.urlsafe_b64encode(session.subject.encode("utf-8")).decode("ascii").rstrip("=")
        parsed_url = urlparse(self.settings.auth_base_url)
        rp_id = parsed_url.hostname or "localhost"
        return {
            "sessionToken": session.token,
            "method": "platform-biometric",
            "challengeId": secrets.token_urlsafe(24),
            "expiresAt": int(time.time()) + self.settings.auth_step_up_ttl_seconds,
            "publicKey": {
                "challenge": base64.urlsafe_b64encode(challenge).decode("ascii").rstrip("="),
                "rp": {"name": "Institución financiera", "id": rp_id},
                "user": {
                    "id": user_id,
                    "name": session.subject,
                    "displayName": "Cliente institucional",
                },
                "pubKeyCredParams": [
                    {"type": "public-key", "alg": -7},
                    {"type": "public-key", "alg": -257},
                ],
                "authenticatorSelection": {
                    "authenticatorAttachment": "platform",
                    "residentKey": "preferred",
                    "userVerification": "required",
                },
                "timeout": 60_000,
                "attestation": "none",
            },
            "authenticationOptions": {
                "challenge": base64.urlsafe_b64encode(challenge).decode("ascii").rstrip("="),
                "rpId": rp_id,
                "userVerification": "required",
                "timeout": 60_000,
            },
        }

    def complete_biometric(self, token: str, assertion: str) -> AuthSession:
        session = self.session(token)
        if not assertion.strip():
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="A biometric assertion is required")
        if self.settings.auth_verification_mode != "local-acceptance":
            raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="Institution WebAuthn provider is not configured")
        expires_at = int(time.time()) + self.settings.auth_step_up_ttl_seconds
        with closing(self._connect()) as db:
            db.execute(
                "UPDATE auth_sessions SET step_up_expires_at = ? WHERE token_hash = ?",
                (expires_at, self._hash_token(token)),
            )
            db.commit()
        return AuthSession(session.token, session.subject, session.channel, session.expires_at, expires_at)

    def logout(self, token: str) -> None:
        with closing(self._connect()) as db:
            db.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (self._hash_token(token),))
            db.commit()

    async def ask_dify(
        self, session_token: str, message: str, conversation_id: str | None = None
    ) -> dict[str, Any]:
        session = self.session(session_token)
        api_key = self.settings.dify_customer_api_key or self.settings.dify_api_key
        if not self.settings.dify_base_url or not api_key:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Web chat is not configured",
            )
        expires_at = min(session.expires_at, int(time.time()) + self.settings.auth_session_ttl_seconds)
        payload: dict[str, Any] = {
            "inputs": {
                "channel": "web",
                "session_state": "VERIFIED",
                "institution_id": self.settings.institution_id,
            },
            "query": message,
            "response_mode": "blocking",
            "user": self._identity_assertion(session.subject, expires_at),
        }
        if conversation_id:
            payload["conversation_id"] = conversation_id
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    f"{self.settings.dify_base_url.rstrip('/')}/v1/chat-messages",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json=payload,
                )
                response.raise_for_status()
                result = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="The web chat is unavailable",
            ) from exc
        answer = result.get("answer")
        if not isinstance(answer, str) or not answer.strip():
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="The web chat returned no answer",
            )
        return {"answer": answer[:8192], "conversationId": result.get("conversation_id")}

    def response_for_session(self, session: AuthSession) -> dict[str, Any]:
        expires_at = min(session.expires_at, int(time.time()) + self.settings.auth_session_ttl_seconds)
        identity_assertion = self._identity_assertion(session.subject, expires_at)
        response: dict[str, Any] = {
            "sessionToken": session.token,
            "sessionState": "VERIFIED",
            "subject": session.subject,
            "expiresAt": expires_at,
            "identityAssertion": identity_assertion,
        }
        if self.settings.dify_identity_secret:
            response["difyIdentitySignature"] = "sha256=" + hmac.new(
                self.settings.dify_identity_secret.encode("utf-8"),
                identity_assertion.encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
        if session.step_up_expires_at and session.step_up_expires_at > int(time.time()):
            response["stepUpExpiresAt"] = session.step_up_expires_at
        return response

    def _identity_assertion(self, subject: str, expires_at: int) -> str:
        encoded_subject = base64.urlsafe_b64encode(subject.encode("utf-8")).decode("ascii").rstrip("=")
        signature = hmac.new(
            self.settings.auth_identity_secret.encode("utf-8"),
            f"{subject}|{expires_at}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return f"banking:v1:{encoded_subject}:{expires_at}:{signature}"

    def _save_whatsapp_identity(self, recipient: str, subject: str, expires_at: int) -> None:
        path = Path(self.settings.whatsapp_state_db)
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path, timeout=10)) as db:
            db.execute("PRAGMA busy_timeout = 10000")
            db.execute(
                "CREATE TABLE IF NOT EXISTS whatsapp_identities ("
                "sender TEXT PRIMARY KEY, subject TEXT NOT NULL, state TEXT NOT NULL, expires_at INTEGER NOT NULL)"
            )
            db.execute(
                "INSERT INTO whatsapp_identities(sender, subject, state, expires_at) VALUES (?, ?, 'VERIFIED', ?) "
                "ON CONFLICT(sender) DO UPDATE SET subject=excluded.subject, state=excluded.state, "
                "expires_at=excluded.expires_at",
                (recipient, subject, expires_at),
            )
            db.commit()

    @staticmethod
    def _hash_token(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _mask_destination(identifier: str, delivery: DeliveryMethod) -> str:
    if delivery == "email":
        local, _, domain = identifier.partition("@")
        return f"{(local[:1] + '***') if local else '***'}@{domain}"
    digits = re.sub(r"[^0-9]", "", identifier)
    return f"***{digits[-4:]}" if len(digits) >= 4 else "***"


def build_auth_router(settings: Settings, auth_flow: AuthFlow) -> APIRouter:
    router = APIRouter(prefix="/channels/auth", tags=["Authentication"])

    @router.post("/start", include_in_schema=False)
    def start_authentication(request: AuthStartRequest) -> dict[str, Any]:
        challenge = auth_flow.start_challenge(request.identifier, request.channel, request.delivery)
        return {
            "challengeId": challenge.challenge_id,
            "delivery": challenge.delivery,
            "deliveryStatus": "sent",
            "maskedDestination": _mask_destination(request.identifier, request.delivery),
            "expiresAt": challenge.expires_at,
            "authUrl": challenge.auth_url,
        }

    @router.post("/verify-otp", include_in_schema=False)
    def verify_otp(request: OtpVerificationRequest) -> dict[str, Any]:
        session = auth_flow.verify_otp(request.challenge_id, request.code)
        return auth_flow.response_for_session(session)

    @router.post("/biometric/start", include_in_schema=False)
    def start_biometric(request: SessionRequest) -> dict[str, Any]:
        return auth_flow.start_biometric(request.session_token)

    @router.post("/biometric/complete", include_in_schema=False)
    def complete_biometric(request: BiometricCompletionRequest) -> dict[str, Any]:
        session = auth_flow.complete_biometric(request.session_token, request.assertion)
        return auth_flow.response_for_session(session)

    @router.post("/chat", include_in_schema=False)
    async def web_chat(request: WebChatRequest) -> dict[str, Any]:
        return await auth_flow.ask_dify(request.session_token, request.message, request.conversation_id)

    @router.get("/status", include_in_schema=False)
    def authentication_status(session_token: str = Query(alias="sessionToken")) -> dict[str, Any]:
        return auth_flow.response_for_session(auth_flow.session(session_token))

    @router.post("/logout", include_in_schema=False)
    def logout(request: SessionRequest) -> dict[str, str]:
        auth_flow.logout(request.session_token)
        return {"status": "logged_out"}

    @router.get("", response_class=HTMLResponse, include_in_schema=False)
    def authentication_page(challenge: str | None = Query(default=None)) -> HTMLResponse:
        return HTMLResponse(_authentication_page(challenge))

    return router


def _authentication_page(challenge: str | None) -> str:
    return f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Verificación de identidad</title>
<style>body{{font-family:system-ui,sans-serif;max-width:30rem;margin:4rem auto;padding:1rem;color:#17202a}}main{{border:1px solid #d8dee4;border-radius:16px;padding:2rem;box-shadow:0 8px 30px #0001}}label{{display:block;margin-top:1rem;font-weight:600}}input,select,textarea,button{{box-sizing:border-box;width:100%;padding:.8rem;margin-top:.4rem;border-radius:8px;border:1px solid #9aa5b1;font-size:1rem}}textarea{{min-height:5rem;resize:vertical}}button{{background:#14532d;color:white;border:0;cursor:pointer;margin-top:1.2rem}}button.secondary{{background:#334155}}#otp,#biometric,#success,#chat{{display:none}}.message{{margin-top:1rem;line-height:1.45}}.error{{color:#b42318}}#chatMessages{{max-height:18rem;overflow:auto;margin-top:1rem}}.bubble{{padding:.7rem;margin:.5rem 0;border-radius:10px;background:#eef2f6}}.bubble.me{{background:#dcfce7}}</style></head>
<body><main><h1>Verifica tu identidad</h1><p class="message">Por seguridad, confirma tu identidad para continuar con tus servicios financieros.</p>
<form id="start"><label for="identifier">Celular o correo registrado</label><input id="identifier" required minlength="3" autocomplete="username"><label for="delivery">Enviar código por</label><select id="delivery"><option value="sms">SMS</option><option value="email">Correo electrónico</option></select><button>Enviar código</button></form>
<section id="otp"><p class="message" id="deliveryMessage"></p><label for="code">Código de verificación</label><input id="code" inputmode="numeric" autocomplete="one-time-code" maxlength="6"><button id="verify">Verificar</button></section>
<section id="biometric"><p class="message">Ahora confirma la operación con la biometría o bloqueo de tu dispositivo.</p><button class="secondary" id="verifyDevice">Verificar dispositivo</button></section>
<section id="success"><p class="message">Identidad verificada. Ya puedes continuar de forma segura.</p></section>
<section id="chat"><h2>Asistente financiero</h2><div id="chatMessages"></div><form id="chatForm"><label for="message">¿En qué podemos ayudarte?</label><textarea id="message" required maxlength="4000"></textarea><button>Enviar</button></form></section><p id="error" class="message error"></p></main>
<script>
let challengeId = {json.dumps(challenge or '')}; let sessionToken = '';
const $ = (id) => document.getElementById(id);
function fromBase64Url(value) {{ const padded=value.replace(/-/g,'+').replace(/_/g,'/') + '='.repeat((4-value.length%4)%4); const binary=atob(padded); return Uint8Array.from(binary, character => character.charCodeAt(0)); }}
function toBase64(value) {{ let binary=''; for (const byte of new Uint8Array(value)) binary += String.fromCharCode(byte); return btoa(binary); }}
function toBase64Url(value) {{ return toBase64(value).replace(/\\+/g,'-').replace(/\\//g,'_').replace(/=+$/,''); }}
function showError(error) {{ $('error').textContent = error; }}
async function post(path, payload) {{ const response = await fetch(path, {{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(payload)}}); const data = await response.json(); if (!response.ok) throw new Error(data.detail || 'No fue posible completar la verificación'); return data; }}
if (challengeId) {{ $('start').style.display='none'; beginChallenge(challengeId, '***'); }}
async function beginChallenge(id, destination) {{ challengeId=id; $('otp').style.display='block'; $('deliveryMessage').textContent='Enviamos un código de verificación a ' + destination + '.'; }}
$('start').addEventListener('submit', async (event) => {{ event.preventDefault(); showError(''); try {{ const data = await post('/channels/auth/start', {{identifier:$('identifier').value,channel:'web',delivery:$('delivery').value}}); await beginChallenge(data.challengeId, data.maskedDestination); $('start').style.display='none'; }} catch (error) {{ showError(error.message); }} }});
$('verify').addEventListener('click', async () => {{ showError(''); try {{ const data = await post('/channels/auth/verify-otp', {{challengeId,code:$('code').value}}); sessionToken=data.sessionToken; $('otp').style.display='none'; $('biometric').style.display='block'; }} catch (error) {{ showError(error.message); }} }});
$('verifyDevice').addEventListener('click', async () => {{ showError(''); try {{ const start=await post('/channels/auth/biometric/start', {{sessionToken}}); let assertion='platform-user-verified'; if (window.PublicKeyCredential && navigator.credentials && start.publicKey) {{ try {{ const storedId=localStorage.getItem('banking-passkey-id'); let credential; if (storedId && start.authenticationOptions) {{ const options=JSON.parse(JSON.stringify(start.authenticationOptions)); options.challenge=fromBase64Url(options.challenge); options.allowCredentials=[{{id:fromBase64Url(storedId),type:'public-key'}}]; credential=await navigator.credentials.get({{publicKey:options}}); }} else {{ const options=JSON.parse(JSON.stringify(start.publicKey)); options.challenge=fromBase64Url(options.challenge); options.user.id=fromBase64Url(options.user.id); credential=await navigator.credentials.create({{publicKey:options}}); if (credential) localStorage.setItem('banking-passkey-id',toBase64Url(credential.rawId)); }} if (credential) assertion=toBase64(credential.response.clientDataJSON); }} catch (_) {{ /* Device without a registered platform credential uses local acceptance. */ }} }} const data=await post('/channels/auth/biometric/complete', {{sessionToken,assertion}}); $('biometric').style.display='none'; $('success').style.display='block'; $('chat').style.display='block'; window.bankingIdentity=data.identityAssertion; }} catch (error) {{ showError(error.message); }} }});
$('chatForm').addEventListener('submit', async (event) => {{ event.preventDefault(); showError(''); const message=$('message').value.trim(); if (!message) return; const bubble=document.createElement('div'); bubble.className='bubble me'; bubble.textContent=message; $('chatMessages').appendChild(bubble); $('message').value=''; try {{ const data=await post('/channels/auth/chat', {{sessionToken,message,conversationId:window.conversationId||null}}); window.conversationId=data.conversationId||window.conversationId; const answer=document.createElement('div'); answer.className='bubble'; answer.textContent=data.answer; $('chatMessages').appendChild(answer); }} catch (error) {{ showError(error.message); }} }});
</script></body></html>"""
