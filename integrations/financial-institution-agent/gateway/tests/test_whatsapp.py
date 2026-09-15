import hashlib
import hmac
import json
import time

from banking_gateway.settings import Settings
from banking_gateway.whatsapp import WhatsAppChannel


def test_whatsapp_signature_uses_meta_hmac() -> None:
    settings = Settings(whatsapp_app_secret="app-secret")
    channel = WhatsAppChannel(settings)
    body = b'{"object":"whatsapp_business_account"}'
    digest = hmac.new(b"app-secret", body, hashlib.sha256).hexdigest()
    assert channel.valid_signature(body, f"sha256={digest}")
    assert not channel.valid_signature(body, "sha256=invalid")


def test_whatsapp_message_claim_is_idempotent(tmp_path) -> None:
    settings = Settings(whatsapp_state_db=str(tmp_path / "state.sqlite3"))
    channel = WhatsAppChannel(settings)
    assert channel.claim_message("wamid-001")
    assert not channel.claim_message("wamid-001")
    channel.release_message("wamid-001")
    assert channel.claim_message("wamid-001")


def test_verified_whatsapp_identity_creates_short_lived_dify_assertion(tmp_path) -> None:
    settings = Settings(
        whatsapp_state_db=str(tmp_path / "state.sqlite3"),
        whatsapp_identity_secret="channel-secret",
    )
    channel = WhatsAppChannel(settings)
    channel.save_identity("593999999999", "core-customer-001", "VERIFIED", int(time.time()) + 300)

    identifier, state = channel.dify_user_identifier("593999999999")
    assert identifier.startswith("banking:v1:")
    assert state == "VERIFIED"


def test_whatsapp_conversations_are_isolated_by_agent_profile(tmp_path) -> None:
    settings = Settings(whatsapp_state_db=str(tmp_path / "state.sqlite3"))
    channel = WhatsAppChannel(settings)

    channel.save_conversation_id("593999999999", "public", "public-conversation")
    channel.save_conversation_id("593999999999", "customer", "customer-conversation")

    assert channel.get_conversation_id("593999999999", "public") == "public-conversation"
    assert channel.get_conversation_id("593999999999", "customer") == "customer-conversation"


def test_identity_signature_can_use_the_dedicated_broker_secret() -> None:
    settings = Settings(whatsapp_app_secret="meta-secret", whatsapp_identity_secret="broker-secret")
    channel = WhatsAppChannel(settings)
    body = json.dumps({"sender": "sender", "subject": "customer"}).encode()
    digest = hmac.new(b"broker-secret", body, hashlib.sha256).hexdigest()
    assert channel.valid_signature(body, f"sha256={digest}", settings.whatsapp_identity_secret)
    assert not channel.valid_signature(body, f"sha256={digest}")
