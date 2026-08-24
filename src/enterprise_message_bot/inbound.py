import asyncio
import hmac
import logging
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

import httpx

from .config import get_settings
from .repository import (
    advance_fidelapp_sales_sequence,
    handle_permission_reply,
    list_pollable_waha_conversations,
    save_incoming_message,
)
from .whatsapp import list_waha_chat_messages

settings = get_settings()
log = logging.getLogger(__name__)


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
        key.get("remoteJidAlt") or data.get("senderPn") or key.get("remoteJid") or ""
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
        "provider": "evolution_api",
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


def parse_waha_message(payload: dict[str, Any]) -> dict[str, Any] | None:
    if str(payload.get("event") or "").strip().lower() != "message":
        return None
    data = payload.get("payload")
    if not isinstance(data, dict) or bool(data.get("fromMe")):
        return None
    raw_data = data.get("_data") if isinstance(data.get("_data"), dict) else {}
    key = raw_data.get("key") if isinstance(raw_data.get("key"), dict) else {}
    remote_jid = str(
        key.get("remoteJidAlt") or data.get("from") or data.get("chatId") or ""
    ).strip()
    provider_message_id = str(data.get("id") or "").strip()
    if not remote_jid or not provider_message_id:
        return None
    if remote_jid.endswith("@g.us") or remote_jid.endswith("@broadcast"):
        return None
    sender_phone = "".join(
        character for character in remote_jid.partition("@")[0] if character.isdigit()
    )
    body = data.get("body")
    return {
        "provider": "waha",
        "instance": str(payload.get("session") or settings.waha_default_session).strip(),
        "provider_message_id": provider_message_id,
        "remote_jid": remote_jid,
        "sender_phone": sender_phone or None,
        "sender_name": str(data.get("pushName") or data.get("notifyName") or "").strip() or None,
        "message_type": "text" if isinstance(body, str) else "unknown",
        "text": body.strip() if isinstance(body, str) and body.strip() else None,
        "received_at": _timestamp(data.get("timestamp")),
        "raw_payload": payload,
    }


def parse_waha_stored_message(
    data: dict[str, Any], *, session: str, expected_phone: str
) -> dict[str, Any] | None:
    payload = {"event": "message", "session": session, "payload": data}
    parsed = parse_waha_message(payload)
    if parsed is None or parsed.get("sender_phone") != expected_phone:
        return None
    parsed["remote_jid"] = f"{expected_phone}@c.us"
    return parsed


async def advance_incoming_conversation(
    result: dict[str, Any], parsed: dict[str, Any]
) -> dict[str, Any]:
    if not result.get("created"):
        return {"action": "ignored", "reason": "duplicate message"}
    conversation_id = result.get("conversation_id")
    if conversation_id:
        sequence = await advance_fidelapp_sales_sequence(str(conversation_id), parsed.get("text"))
        if sequence.get("reason") != "not a FidelApp sales sequence":
            return sequence
    return await handle_permission_reply(
        parsed.get("sender_phone"),
        parsed.get("text"),
        provider=parsed.get("provider"),
        session_name=parsed.get("instance"),
    )


async def poll_waha_incoming_once() -> int:
    processed = 0
    for conversation in await list_pollable_waha_conversations():
        messages = await list_waha_chat_messages(
            conversation["phone"], session=conversation["session"]
        )
        parsed_messages = []
        for message in messages:
            parsed = parse_waha_stored_message(
                message,
                session=conversation["session"],
                expected_phone=conversation["phone"],
            )
            if parsed and parsed["received_at"] >= conversation["created_at"]:
                parsed_messages.append(parsed)
        for parsed in sorted(parsed_messages, key=lambda item: item["received_at"]):
            result = await save_incoming_message(parsed)
            if result.get("created"):
                await advance_incoming_conversation(result, parsed)
                processed += 1
    return processed


async def run_waha_inbound_poller() -> None:
    log.info("Persistent WAHA inbound poller started")
    while True:
        try:
            await poll_waha_incoming_once()
            await asyncio.sleep(settings.waha_poll_interval_seconds)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("WAHA inbound polling iteration failed")
            await asyncio.sleep(settings.waha_poll_interval_seconds)


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


def waha_webhook_request_is_authorized(headers: list[tuple[bytes, bytes]]) -> bool:
    expected = settings.waha_webhook_secret
    if not expected or not expected.get_secret_value():
        return settings.app_environment.strip().lower() == "development"
    values = {name.lower(): value.decode("latin-1") for name, value in headers}
    supplied = values.get(b"x-waha-webhook-secret", "").strip()
    return bool(supplied and hmac.compare_digest(supplied, expected.get_secret_value()))


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
