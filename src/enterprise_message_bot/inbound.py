import hmac
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

import httpx

from .config import get_settings

settings = get_settings()


def _event_name(payload: dict[str, Any]) -> str:
    return str(payload.get("event") or "").strip().upper().replace(".", "_").replace("-", "_")


def _first_text(message: dict[str, Any]) -> tuple[str, str | None]:
    candidates = (
        ("conversation", message.get("conversation")),
        ("extendedTextMessage", (message.get("extendedTextMessage") or {}).get("text")),
        ("imageMessage", (message.get("imageMessage") or {}).get("caption")),
        ("videoMessage", (message.get("videoMessage") or {}).get("caption")),
        ("documentMessage", (message.get("documentMessage") or {}).get("caption")),
        (
            "buttonsResponseMessage",
            (message.get("buttonsResponseMessage") or {}).get("selectedDisplayText"),
        ),
        (
            "templateButtonReplyMessage",
            (message.get("templateButtonReplyMessage") or {}).get("selectedDisplayText"),
        ),
        ("listResponseMessage", (message.get("listResponseMessage") or {}).get("title")),
    )
    for message_type, value in candidates:
        if isinstance(value, str) and value.strip():
            return message_type, value.strip()
    message_type = next((str(key) for key in message if key != "messageContextInfo"), "unknown")
    return message_type, None


def _timestamp(value: Any) -> datetime:
    try:
        numeric = float(value)
        if numeric > 10_000_000_000:
            numeric /= 1000
        return datetime.fromtimestamp(numeric, tz=UTC)
    except (TypeError, ValueError, OSError):
        return datetime.now(UTC)


def parse_evolution_message(payload: dict[str, Any]) -> dict[str, Any] | None:
    if _event_name(payload) != "MESSAGES_UPSERT":
        return None
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    key = data.get("key")
    message = data.get("message")
    if not isinstance(key, dict) or not isinstance(message, dict) or bool(key.get("fromMe")):
        return None

    remote_jid = str(
        key.get("remoteJidAlt")
        or data.get("senderPn")
        or key.get("remoteJid")
        or ""
    ).strip()
    provider_message_id = str(key.get("id") or "").strip()
    if not remote_jid or not provider_message_id:
        return None
    if remote_jid.endswith("@g.us") or remote_jid.endswith("@broadcast"):
        return None

    jid_user = remote_jid.partition("@")[0]
    sender_phone = "".join(character for character in jid_user if character.isdigit()) or None
    message_type, text = _first_text(message)
    return {
        "instance": str(payload.get("instance") or settings.evolution_api_instance).strip(),
        "provider_message_id": provider_message_id,
        "remote_jid": remote_jid,
        "sender_phone": sender_phone,
        "sender_name": str(data.get("pushName") or "").strip() or None,
        "message_type": message_type,
        "text": text,
        "received_at": _timestamp(data.get("messageTimestamp")),
        "raw_payload": payload,
    }


def webhook_request_is_authorized(headers: list[tuple[bytes, bytes]]) -> bool:
    expected = settings.resolved_evolution_webhook_secret
    if not expected:
        return settings.app_environment.strip().lower() == "development"
    values = {name.lower(): value.decode("latin-1") for name, value in headers}
    authorization = values.get(b"authorization", "")
    scheme, separator, bearer = authorization.partition(" ")
    supplied = bearer.strip() if separator and scheme.lower() == "bearer" else ""
    supplied = supplied or values.get(b"x-evolution-webhook-secret", "").strip()
    return bool(supplied and hmac.compare_digest(supplied, expected))


async def configure_evolution_webhook() -> dict[str, Any]:
    webhook_url = settings.resolved_evolution_webhook_url
    if not webhook_url:
        return {"configured": False, "reason": "public webhook URL is not configured"}
    if not settings.evolution_api_base_url or not settings.evolution_api_key:
        return {"configured": False, "reason": "Evolution API is not configured"}

    instance = quote(settings.evolution_api_instance.strip(), safe="")
    endpoint = f"{settings.evolution_api_base_url.rstrip('/')}/webhook/set/{instance}"
    secret = settings.resolved_evolution_webhook_secret
    headers = {
        "Content-Type": "application/json",
        "apikey": settings.evolution_api_key.get_secret_value(),
    }
    payload = {
        "webhook": {
            "enabled": True,
            "url": webhook_url,
            "events": ["MESSAGES_UPSERT"],
            "headers": {"Authorization": f"Bearer {secret}"} if secret else {},
            "base64": False,
            "webhookByEvents": False,
            "webhookBase64": False,
        }
    }
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(endpoint, headers=headers, json=payload)
        response.raise_for_status()
    return {"configured": True, "url": webhook_url, "instance": settings.evolution_api_instance}
