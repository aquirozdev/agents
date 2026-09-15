import base64
import hashlib
import hmac
import json
import re
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request, Response, status

from .settings import Settings


router = APIRouter(prefix="/channels/whatsapp", tags=["WhatsApp"])


class WhatsAppChannel:
    """Meta WhatsApp Cloud API adapter that forwards messages to Dify.

    The adapter deliberately does not know a banking core. Private banking
    data remains unavailable until the institution's identity middleware marks
    the conversation as verified.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _connect(self) -> sqlite3.Connection:
        Path(self.settings.whatsapp_state_db).parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.settings.whatsapp_state_db, timeout=10)
        db.execute("PRAGMA busy_timeout = 10000")
        db.execute("PRAGMA journal_mode = WAL")
        db.execute(
            "CREATE TABLE IF NOT EXISTS whatsapp_conversations_v2 "
            "(sender TEXT NOT NULL, agent_profile TEXT NOT NULL, conversation_id TEXT NOT NULL, "
            "PRIMARY KEY(sender, agent_profile))"
        )
        db.execute(
            "CREATE TABLE IF NOT EXISTS whatsapp_messages "
            "(message_id TEXT PRIMARY KEY, status TEXT NOT NULL)"
        )
        db.execute(
            "CREATE TABLE IF NOT EXISTS whatsapp_identities "
            "(sender TEXT PRIMARY KEY, subject TEXT NOT NULL, state TEXT NOT NULL, expires_at INTEGER NOT NULL)"
        )
        return db

    def ensure_enabled(self) -> None:
        if not self.settings.whatsapp_enabled:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="WhatsApp channel is disabled")
        required = {
            "WHATSAPP_VERIFY_TOKEN": self.settings.whatsapp_verify_token,
            "WHATSAPP_APP_SECRET": self.settings.whatsapp_app_secret,
            "WHATSAPP_ACCESS_TOKEN": self.settings.whatsapp_access_token,
            "WHATSAPP_PHONE_NUMBER_ID": self.settings.whatsapp_phone_number_id,
            "DIFY_BASE_URL": self.settings.dify_base_url,
        }
        if not any(
            (self.settings.dify_public_api_key, self.settings.dify_customer_api_key, self.settings.dify_api_key)
        ):
            required["DIFY_API_KEY"] = None
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"WhatsApp channel is not configured: {', '.join(missing)}",
            )

    def valid_signature(self, body: bytes, signature: str | None, secret: str | None = None) -> bool:
        signing_secret = secret or self.settings.whatsapp_app_secret
        if not signature or not signing_secret:
            return False
        expected = "sha256=" + hmac.new(
            signing_secret.encode(), body, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, signature)

    def save_identity(self, sender: str, subject: str, state: str, expires_at: int) -> None:
        if state not in {"PUBLIC", "VERIFIED"}:
            raise ValueError("Identity state must be PUBLIC or VERIFIED")
        if not sender or not subject or any(character in sender + subject for character in "\r\n"):
            raise ValueError("Identity sender and subject are invalid")
        if expires_at <= int(time.time()) or expires_at > int(time.time()) + 86_400:
            raise ValueError("Identity expiry must be within the next 24 hours")
        with closing(self._connect()) as db:
            db.execute(
                "INSERT INTO whatsapp_identities(sender, subject, state, expires_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(sender) DO UPDATE SET subject=excluded.subject, state=excluded.state, "
                "expires_at=excluded.expires_at",
                (sender, subject, state, expires_at),
            )
            db.commit()

    def get_identity(self, sender: str) -> tuple[str, str, int] | None:
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT subject, state, expires_at FROM whatsapp_identities WHERE sender = ?", (sender,)
            ).fetchone()
        if not row or int(row[2]) <= int(time.time()):
            return None
        return str(row[0]), str(row[1]), int(row[2])

    def dify_user_identifier(self, sender: str) -> tuple[str, str]:
        """Return a Dify user identifier and trusted channel state.

        The assertion is carried inside the external user id because Dify's
        API-tool invocation only knows that persisted value. The identity
        proxy verifies it with the separate institution secret.
        """

        identity = self.get_identity(sender)
        if not identity or identity[1] != "VERIFIED" or not self.settings.whatsapp_identity_secret:
            return f"{self.settings.dify_application_user_prefix}:{sender}", "PUBLIC"
        subject, _, expires_at = identity
        encoded_subject = base64.urlsafe_b64encode(subject.encode("utf-8")).decode("ascii").rstrip("=")
        message = f"{subject}|{expires_at}"
        signature = hmac.new(
            self.settings.whatsapp_identity_secret.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        identifier = f"banking:v1:{encoded_subject}:{expires_at}:{signature}"
        # Dify persists external_user_id in a 255-character column. Never let
        # a long institution subject be silently truncated into a bad token.
        if len(identifier) > 255:
            return f"{self.settings.dify_application_user_prefix}:{sender}", "PUBLIC"
        return identifier, "VERIFIED"

    def get_conversation_id(self, sender: str, agent_profile: str) -> str | None:
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT conversation_id FROM whatsapp_conversations_v2 "
                "WHERE sender = ? AND agent_profile = ?",
                (sender, agent_profile),
            ).fetchone()
            return row[0] if row else None

    def save_conversation_id(self, sender: str, agent_profile: str, conversation_id: str) -> None:
        with closing(self._connect()) as db:
            db.execute(
                "INSERT INTO whatsapp_conversations_v2(sender, agent_profile, conversation_id) VALUES (?, ?, ?) "
                "ON CONFLICT(sender, agent_profile) DO UPDATE SET conversation_id=excluded.conversation_id",
                (sender, agent_profile, conversation_id),
            )
            db.commit()

    def claim_message(self, message_id: str) -> bool:
        """Claim a Meta delivery exactly once for this state store."""

        with closing(self._connect()) as db:
            cursor = db.execute(
                "INSERT OR IGNORE INTO whatsapp_messages(message_id, status) VALUES (?, 'processing')",
                (message_id,),
            )
            db.commit()
            return cursor.rowcount == 1

    def complete_message(self, message_id: str) -> None:
        with closing(self._connect()) as db:
            db.execute(
                "UPDATE whatsapp_messages SET status='processed' WHERE message_id = ?", (message_id,)
            )
            db.commit()

    def release_message(self, message_id: str) -> None:
        with closing(self._connect()) as db:
            db.execute("DELETE FROM whatsapp_messages WHERE message_id = ?", (message_id,))
            db.commit()

    def extract_messages(self, payload: dict[str, Any]) -> list[tuple[str, str, str]]:
        messages: list[tuple[str, str, str]] = []
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                for message in value.get("messages", []):
                    if message.get("type") != "text":
                        continue
                    sender = message.get("from")
                    message_id = message.get("id")
                    text = message.get("text", {}).get("body")
                    if sender and message_id and text:
                        messages.append((sender, message_id, text))
        return messages

    async def ask_dify(self, sender: str, text: str, correlation_id: str | None = None) -> str:
        if not self.settings.dify_base_url:
            raise RuntimeError("Dify API is not configured")
        dify_user, session_state = self.dify_user_identifier(sender)
        agent_profile = "customer" if session_state == "VERIFIED" else "public"
        conversation_id = self.get_conversation_id(sender, agent_profile)
        api_key = (
            (self.settings.dify_customer_api_key or self.settings.dify_api_key)
            if agent_profile == "customer"
            else (self.settings.dify_public_api_key or self.settings.dify_api_key)
        )
        if not api_key:
            raise RuntimeError(f"Dify API key is not configured for the {agent_profile} agent")
        payload: dict[str, Any] = {
            "inputs": {
                "channel": "whatsapp",
                "session_state": session_state,
                "agent_profile": agent_profile,
                "institution_id": self.settings.institution_id,
            },
            "query": text,
            "response_mode": "blocking",
            "user": dify_user,
        }
        if conversation_id:
            payload["conversation_id"] = conversation_id
        headers = {"Authorization": f"Bearer {api_key}"}
        if correlation_id:
            headers["X-Correlation-ID"] = correlation_id
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{self.settings.dify_base_url.rstrip('/')}/v1/chat-messages",
                headers=headers,
                json=payload,
            )
        response.raise_for_status()
        result = response.json()
        new_conversation_id = result.get("conversation_id")
        if new_conversation_id:
            self.save_conversation_id(sender, agent_profile, new_conversation_id)
        answer = result.get("answer")
        if not isinstance(answer, str) or not answer.strip():
            raise RuntimeError("Dify returned no answer")
        return answer[:4096]

    async def send_text(self, recipient: str, text: str) -> None:
        if not self.settings.whatsapp_access_token or not self.settings.whatsapp_phone_number_id:
            raise RuntimeError("WhatsApp Cloud API is not configured")
        url = (
            f"https://graph.facebook.com/{self.settings.whatsapp_graph_api_version}/"
            f"{self.settings.whatsapp_phone_number_id}/messages"
        )
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                url,
                headers={"Authorization": f"Bearer {self.settings.whatsapp_access_token}"},
                json={
                    "messaging_product": "whatsapp",
                    "to": recipient,
                    "type": "text",
                    "text": {"preview_url": False, "body": text},
                },
            )
        response.raise_for_status()

    async def send_authentication_prompt(self, recipient: str, auth_url: str) -> None:
        await self.send_text(
            recipient,
            "Por seguridad, primero verifica tu identidad en el enlace seguro de la institución:\n"
            f"{auth_url}\n\n"
            "No compartas códigos, claves ni datos sensibles por este chat.",
        )


def build_router(settings: Settings, auth_flow: Any | None = None) -> APIRouter:
    channel = WhatsAppChannel(settings)

    @router.get("/webhook")
    async def verify_webhook(request: Request) -> Response:
        channel.ensure_enabled()
        query = request.query_params
        if query.get("hub.mode") != "subscribe" or query.get("hub.verify_token") != settings.whatsapp_verify_token:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid webhook verification")
        return Response(content=query.get("hub.challenge", ""), media_type="text/plain")

    @router.post("/webhook", status_code=status.HTTP_200_OK)
    async def receive_webhook(request: Request) -> dict[str, str]:
        channel.ensure_enabled()
        body = await request.body()
        if not channel.valid_signature(body, request.headers.get("X-Hub-Signature-256")):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid webhook signature")
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON payload") from exc
        if payload.get("object") not in {None, "whatsapp_business_account"}:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported webhook object")
        for sender, message_id, text in channel.extract_messages(payload):
            if not channel.claim_message(message_id):
                continue
            try:
                normalized_text = text.strip().lower()
                if auth_flow is not None:
                    pending = auth_flow.pending_challenge(sender, "whatsapp")
                    if pending and re.fullmatch(r"\d{6}", text.strip()):
                        try:
                            auth_flow.verify_otp(pending.challenge_id, text.strip())
                            await channel.send_text(
                                sender,
                                "Identidad verificada. Ya puedes continuar con tus consultas financieras.",
                            )
                        except HTTPException:
                            await channel.send_text(
                                sender,
                                "El código no es válido o ya expiró. Usa nuevamente el enlace seguro para solicitar otro.",
                            )
                        channel.complete_message(message_id)
                        continue
                    if normalized_text in {"autenticar", "verificar", "iniciar sesión", "iniciar sesion"}:
                        try:
                            challenge = auth_flow.start_challenge(sender, "whatsapp", "sms")
                            await channel.send_authentication_prompt(sender, challenge.auth_url)
                        except HTTPException:
                            await channel.send_text(
                                sender,
                                "No pude encontrar una relación activa con este celular. Solicita atención a la institución.",
                            )
                        channel.complete_message(message_id)
                        continue
                answer = await channel.ask_dify(
                    sender,
                    text,
                    getattr(request.state, "correlation_id", None),
                )
            except Exception:
                answer = "No puedo completar la consulta en este momento. Por favor intenta nuevamente o solicita atención humana."
            try:
                await channel.send_text(sender, answer)
            except Exception:
                channel.release_message(message_id)
                raise
            channel.complete_message(message_id)
        return {"status": "accepted"}

    @router.post("/identity", status_code=status.HTTP_202_ACCEPTED, include_in_schema=False)
    async def update_identity(request: Request) -> dict[str, str]:
        channel.ensure_enabled()
        if not settings.whatsapp_identity_secret:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="WhatsApp identity relay is not configured",
            )
        body = await request.body()
        if not channel.valid_signature(
            body,
            request.headers.get("X-Institution-Identity-Signature"),
            settings.whatsapp_identity_secret,
        ):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid identity signature")
        try:
            payload = json.loads(body)
            sender = str(payload["sender"])
            subject = str(payload["subject"])
            state = str(payload.get("state", "VERIFIED")).upper()
            expires_at = int(payload["expiresAt"])
            channel.save_identity(sender, subject, state, expires_at)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid identity payload") from exc
        return {"status": "accepted"}

    return router
