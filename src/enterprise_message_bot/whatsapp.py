import base64
import json
import mimetypes
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from .config import get_settings

settings = get_settings()


class MissingWhatsAppConfiguration(RuntimeError):
    pass


def build_evolution_request(recipient: str, body: str, session: str | None = None):
    instance = (session or settings.evolution_api_instance).strip()
    if (
        not settings.evolution_api_base_url
        or not settings.evolution_api_key
        or not settings.evolution_api_key.get_secret_value()
        or not instance
    ):
        raise MissingWhatsAppConfiguration("Evolution API is not fully configured")
    return (
        f"{settings.evolution_api_base_url.rstrip('/')}/message/sendText/"
        f"{quote(instance, safe='')}",
        {
            "Content-Type": "application/json",
            "apikey": settings.evolution_api_key.get_secret_value(),
        },
        {
            "number": recipient,
            "text": body,
            "delay": settings.evolution_api_delay_ms,
            "linkPreview": settings.evolution_api_link_preview,
        },
    )


def build_waha_request(recipient: str, body: str, session: str | None = None):
    session_name = (session or settings.waha_default_session).strip()
    if (
        not settings.waha_api_base_url
        or not settings.waha_api_key
        or not settings.waha_api_key.get_secret_value()
        or not session_name
    ):
        raise MissingWhatsAppConfiguration("WAHA is not fully configured")
    return (
        f"{settings.waha_api_base_url.rstrip('/')}/api/sendText",
        {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-Api-Key": settings.waha_api_key.get_secret_value(),
        },
        {"session": session_name, "chatId": f"{recipient}@c.us", "text": body},
    )


def whatsapp_provider_status() -> dict[str, Any]:
    evolution_ready = bool(
        settings.evolution_api_base_url
        and settings.evolution_api_key
        and settings.evolution_api_key.get_secret_value()
        and settings.evolution_api_instance
    )
    waha_ready = bool(
        settings.waha_api_base_url
        and settings.waha_api_key
        and settings.waha_api_key.get_secret_value()
        and settings.waha_default_session
    )
    return {
        "configured": (settings.whatsapp_provider == "evolution_api" and evolution_ready)
        or (settings.whatsapp_provider == "waha" and waha_ready),
        "default_provider": settings.whatsapp_provider,
        "providers": {
            "evolution_api": {
                "configured": evolution_ready,
                "sessions": [settings.evolution_api_instance],
            },
            "waha": {
                "configured": waha_ready,
                "sessions": settings.configured_waha_sessions,
            },
        },
    }


async def send_whatsapp(
    recipient: str,
    body: str,
    *,
    provider: str | None = None,
    session: str | None = None,
) -> str:
    selected = (provider or settings.whatsapp_provider).strip().lower()
    if selected == "evolution_api":
        endpoint, headers, payload = build_evolution_request(recipient, body, session)
    elif selected == "waha":
        endpoint, headers, payload = build_waha_request(recipient, body, session)
    else:
        raise MissingWhatsAppConfiguration("No WhatsApp provider is selected")
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            endpoint,
            headers=headers,
            content=json.dumps(payload, ensure_ascii=True, separators=(",", ":")),
        )
        response.raise_for_status()
        data = response.json()
    if not isinstance(data, dict):
        return f"{selected}-accepted"
    key = data.get("key") if isinstance(data.get("key"), dict) else {}
    nested_data = data.get("_data") if isinstance(data.get("_data"), dict) else {}
    return str(
        key.get("id")
        or data.get("messageId")
        or data.get("id")
        or nested_data.get("id")
        or f"{selected}-accepted"
    )


async def send_whatsapp_file(
    recipient: str,
    file_path: str | Path,
    *,
    caption: str = "",
    session: str | None = None,
) -> str:
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"WhatsApp attachment not found: {path}")
    endpoint, headers, base_payload = build_waha_request(recipient, caption, session)
    endpoint = endpoint.removesuffix("/sendText") + "/sendFile"
    mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    payload = {
        "session": base_payload["session"],
        "chatId": base_payload["chatId"],
        "caption": caption,
        "file": {
            "mimetype": mime_type,
            "filename": path.name,
            "data": base64.b64encode(path.read_bytes()).decode("ascii"),
        },
    }
    async with httpx.AsyncClient(timeout=90) as client:
        response = await client.post(
            endpoint,
            headers=headers,
            content=json.dumps(payload, ensure_ascii=True, separators=(",", ":")),
        )
        response.raise_for_status()
        data = response.json()
    if not isinstance(data, dict):
        return "waha-file-accepted"
    key = data.get("key") if isinstance(data.get("key"), dict) else {}
    return str(data.get("id") or key.get("id") or "waha-file-accepted")


async def list_waha_chat_messages(
    phone: str, *, session: str | None = None, limit: int = 30
) -> list[dict[str, Any]]:
    _, headers, payload = build_waha_request(phone, "", session)
    encoded_session = quote(str(payload["session"]), safe="")
    encoded_chat = quote(str(payload["chatId"]), safe="")
    endpoint = (
        f"{settings.waha_api_base_url.rstrip('/')}/api/{encoded_session}/chats/"
        f"{encoded_chat}/messages"
    )
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(
            endpoint,
            headers=headers,
            params={"limit": min(max(limit, 1), 100), "offset": 0, "downloadMedia": "false"},
        )
        response.raise_for_status()
        data = response.json()
    if not isinstance(data, list):
        raise RuntimeError("WAHA returned an unexpected messages response")
    return [item for item in data if isinstance(item, dict)]


async def list_waha_sessions() -> list[dict[str, Any]]:
    if not settings.waha_api_base_url or not settings.waha_api_key:
        raise MissingWhatsAppConfiguration("WAHA is not configured")
    headers = {"X-Api-Key": settings.waha_api_key.get_secret_value(), "Accept": "application/json"}
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(
            f"{settings.waha_api_base_url.rstrip('/')}/api/sessions", headers=headers
        )
        response.raise_for_status()
        payload = response.json()
    if not isinstance(payload, list):
        raise RuntimeError("WAHA returned an unexpected sessions response")
    return payload


async def configure_waha_webhooks() -> dict[str, Any]:
    webhook_url = settings.resolved_waha_webhook_url
    secret = settings.waha_webhook_secret
    if not webhook_url:
        return {"configured": False, "reason": "public WAHA webhook URL is not configured"}
    if not settings.waha_api_base_url or not settings.waha_api_key:
        return {"configured": False, "reason": "WAHA is not configured"}
    if not secret or not secret.get_secret_value():
        return {"configured": False, "reason": "WAHA_WEBHOOK_SECRET is not configured"}

    headers = {
        "X-Api-Key": settings.waha_api_key.get_secret_value(),
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    configured = []
    async with httpx.AsyncClient(timeout=30) as client:
        for session_name in settings.configured_waha_sessions:
            encoded_session = quote(session_name, safe="")
            endpoint = f"{settings.waha_api_base_url.rstrip('/')}/api/sessions/{encoded_session}"
            response = await client.get(endpoint, headers=headers)
            response.raise_for_status()
            current = response.json()
            config = dict(current.get("config") or {}) if isinstance(current, dict) else {}
            webhooks = [
                item
                for item in config.get("webhooks", [])
                if isinstance(item, dict) and item.get("url") != webhook_url
            ]
            webhooks.append(
                {
                    "url": webhook_url,
                    "events": ["message"],
                    "customHeaders": [
                        {
                            "name": "X-WAHA-Webhook-Secret",
                            "value": secret.get_secret_value(),
                        }
                    ],
                    "retries": {"policy": "exponential", "delaySeconds": 2, "attempts": 8},
                }
            )
            config["webhooks"] = webhooks
            update = await client.put(
                endpoint,
                headers=headers,
                content=json.dumps(
                    {"name": session_name, "config": config},
                    ensure_ascii=True,
                    separators=(",", ":"),
                ),
            )
            update.raise_for_status()
            configured.append(session_name)
    return {"configured": True, "url": webhook_url, "sessions": configured}
