import asyncio
import hmac
import json
import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any

import mcp.types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from .browser import (
    SOURCE_TYPES,
    browser_status,
    stop_browser,
    validate_source_type,
)
from .config import get_settings
from .conversation import campaign_delay_seconds
from .database import close_database, database_health
from .dispatcher import run_outbound_dispatcher
from .inbound import (
    advance_incoming_conversation,
    configure_evolution_webhook,
    parse_evolution_message,
    parse_waha_message,
    run_waha_inbound_poller,
    waha_webhook_request_is_authorized,
    webhook_request_is_authorized,
)
from .logging_config import configure_logging
from .outreach import (
    _send_smtp,
    normalize_benin_phone,
    preview_message,
    provider_status,
    send_message,
)
from .registry_client import fetch_registry_page
from .repository import (
    acknowledge_incoming_messages,
    complete_incoming_reply,
    get_whatsapp_conversation,
    list_companies,
    list_contact_history,
    list_incoming_messages,
    list_runs,
    list_whatsapp_conversations,
    next_whatsapp_campaign_start,
    plan_whatsapp_message,
    record_whatsapp_outbound,
    repository_stats,
    reserve_incoming_reply,
    resume_fidelapp_sales_sequence,
    save_incoming_message,
    save_registry_page,
    saved_page_numbers,
    set_do_not_contact,
    set_whatsapp_conversation_mode,
    start_fidelapp_sales_sequence,
    update_planned_whatsapp_message,
)
from .whatsapp import configure_waha_webhooks, list_waha_sessions, send_whatsapp

settings = get_settings()
app = Server(settings.app_name)
log = logging.getLogger(__name__)
_tool_lock = asyncio.Lock()
SERVICE_VERSION = "0.5.2"

SOURCE_SCHEMA = {
    "type": "string",
    "enum": ["companies", "establishments"],
    "description": "companies = societes, establishments = etablissements individuels",
}
SOURCE_OR_ALL_SCHEMA = {
    "type": "string",
    "enum": ["companies", "establishments", "all"],
    "default": "all",
    "description": (
        "Client source: companies = societes, establishments = etablissements "
        "individuels, all = both registries"
    ),
}
CHANNEL_SCHEMA = {"type": "string", "enum": ["email", "whatsapp"]}


def _json_content(payload: Any) -> list[types.TextContent]:
    return [
        types.TextContent(type="text", text=json.dumps(payload, ensure_ascii=False, default=str))
    ]


def _optional_text(args: dict[str, Any], name: str) -> str | None:
    value = str(args.get(name, "")).strip()
    return value or None


def _optional_date(args: dict[str, Any], name: str) -> date | None:
    value = _optional_text(args, name)
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name} must use YYYY-MM-DD") from exc


def _optional_datetime(args: dict[str, Any], name: str) -> datetime | None:
    value = _optional_text(args, name)
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{name} must use ISO 8601") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _target_filters(args: dict[str, Any], *, channel: str | None = None) -> dict[str, Any]:
    return {
        "query": _optional_text(args, "query"),
        "source_type": args.get("source_type", "all"),
        "city": _optional_text(args, "city"),
        "district": _optional_text(args, "district"),
        "activity": _optional_text(args, "activity"),
        "created_from": _optional_date(args, "created_from"),
        "created_to": _optional_date(args, "created_to"),
        "channel": channel or args.get("channel"),
        "contact_status": args.get("contact_status", "all"),
        "include_do_not_contact": bool(args.get("include_do_not_contact", False)),
        "limit": int(args.get("limit", 50)),
    }


def _filter_properties() -> dict[str, Any]:
    return {
        "query": {"type": "string", "maxLength": 300},
        "source_type": SOURCE_OR_ALL_SCHEMA,
        "city": {"type": "string", "maxLength": 200},
        "district": {"type": "string", "maxLength": 300},
        "activity": {"type": "string", "maxLength": 500},
        "created_from": {"type": "string", "format": "date"},
        "created_to": {"type": "string", "format": "date"},
        "channel": CHANNEL_SCHEMA,
        "contact_status": {
            "type": "string",
            "enum": ["all", "contacted", "uncontacted"],
            "default": "all",
        },
        "include_do_not_contact": {"type": "boolean", "default": False},
        "limit": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 50},
    }


@app.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="health_check",
            description="Check MCP, PostgreSQL, browser and outbound provider configuration.",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="inspect_registry",
            description="Inspect either public directory and return a small visible sample.",
            inputSchema={
                "type": "object",
                "required": ["source_type"],
                "properties": {
                    "source_type": SOURCE_SCHEMA,
                    "refresh": {"type": "boolean", "default": False},
                    "sample_size": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5},
                },
            },
        ),
        types.Tool(
            name="search_registry",
            description=(
                "Search clients online across both registries by default. Clients are either "
                "companies (societes) or establishments (etablissements individuels)."
            ),
            inputSchema={
                "type": "object",
                "required": ["query"],
                "properties": {
                    "source_type": SOURCE_OR_ALL_SCHEMA,
                    "query": {"type": "string", "minLength": 1, "maxLength": 300},
                    "save_results": {"type": "boolean", "default": True},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 20},
                },
            },
        ),
        types.Tool(
            name="collect_registry_pages",
            description=(
                "Collect and cache a bounded page range. "
                "Already saved pages are skipped by default."
            ),
            inputSchema={
                "type": "object",
                "required": ["source_type"],
                "properties": {
                    "source_type": SOURCE_OR_ALL_SCHEMA,
                    "start_page": {"type": "integer", "minimum": 1, "default": 1},
                    "max_pages": {"type": "integer", "minimum": 1, "maximum": 100, "default": 1},
                    "page_size": {
                        "type": "integer",
                        "enum": [10, 15, 20, 30, 50, 100, 200, 1000],
                        "default": 100,
                    },
                    "skip_existing": {"type": "boolean", "default": True},
                },
            },
        ),
        types.Tool(
            name="find_saved_targets",
            description=(
                "Filter cached clients from both companies and individual establishments by "
                "city, district, activity, creation date and contact status."
            ),
            inputSchema={"type": "object", "properties": _filter_properties()},
        ),
        types.Tool(
            name="preview_targeted_messages",
            description="Render a message campaign without sending anything.",
            inputSchema={
                "type": "object",
                "required": ["channel", "body_template"],
                "properties": {
                    **_filter_properties(),
                    "channel": CHANNEL_SCHEMA,
                    "subject_template": {"type": "string", "maxLength": 500},
                    "body_template": {"type": "string", "minLength": 1, "maxLength": 10000},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
                },
            },
        ),
        types.Tool(
            name="send_targeted_messages",
            description=(
                "Send a bounded email or WhatsApp campaign. confirm_send must be true. "
                "Previously contacted recipients are automatically skipped."
            ),
            inputSchema={
                "type": "object",
                "required": ["channel", "body_template", "confirm_send"],
                "properties": {
                    **_filter_properties(),
                    "channel": CHANNEL_SCHEMA,
                    "subject_template": {"type": "string", "maxLength": 500},
                    "body_template": {"type": "string", "minLength": 1, "maxLength": 10000},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
                    "confirm_send": {"type": "boolean", "const": True},
                },
            },
        ),
        types.Tool(
            name="send_whatsapp_message",
            description=(
                "Send one short direct WhatsApp message to an explicit phone number. Use at "
                "most once after reply_to_incoming_message, then wait for a new inbound reply. "
                "This bypasses campaign targeting and does not mark a company as contacted."
            ),
            inputSchema={
                "type": "object",
                "required": ["number", "text", "confirm_send"],
                "properties": {
                    "number": {"type": "string", "minLength": 6, "maxLength": 40},
                    "text": {"type": "string", "minLength": 1, "maxLength": 240},
                    "provider": {"type": "string", "enum": ["evolution_api", "waha"]},
                    "session": {"type": "string", "minLength": 1, "maxLength": 200},
                    "confirm_send": {"type": "boolean", "const": True},
                },
            },
        ),
        types.Tool(
            name="list_whatsapp_sessions",
            description="List the WhatsApp numbers/sessions currently known by WAHA.",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="start_fidelapp_sales_sequence",
            description=(
                "Start or cleanly restart the approved FidelApp store outreach sequence. Cancel "
                "pending automatic steps, send only the personalized identity opener, then wait "
                "for an inbound reply before every next step."
            ),
            inputSchema={
                "type": "object",
                "required": ["number", "company_name", "confirm_send"],
                "properties": {
                    "number": {"type": "string", "minLength": 6, "maxLength": 40},
                    "company_name": {"type": "string", "maxLength": 500},
                    "provider": {
                        "type": "string",
                        "enum": ["evolution_api", "waha"],
                        "default": "waha",
                    },
                    "session": {"type": "string", "default": "default", "maxLength": 200},
                    "confirm_send": {"type": "boolean", "const": True},
                },
            },
        ),
        types.Tool(
            name="resume_fidelapp_sales_sequence",
            description=(
                "Adopt an existing FidelApp WhatsApp discussion directly in AI mode without "
                "sending or repeating any onboarding message."
            ),
            inputSchema={
                "type": "object",
                "required": ["number", "company_name", "confirm"],
                "properties": {
                    "number": {"type": "string", "minLength": 6, "maxLength": 40},
                    "company_name": {"type": "string", "maxLength": 500},
                    "provider": {
                        "type": "string",
                        "enum": ["evolution_api", "waha"],
                        "default": "waha",
                    },
                    "session": {"type": "string", "default": "default", "maxLength": 200},
                    "confirm": {"type": "boolean", "const": True},
                },
            },
        ),
        types.Tool(
            name="list_whatsapp_conversations",
            description="List persistent conversations across all WhatsApp numbers and providers.",
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50}
                },
            },
        ),
        types.Tool(
            name="get_whatsapp_conversation",
            description="Read one conversation and its complete ordered message history.",
            inputSchema={
                "type": "object",
                "required": ["conversation_id"],
                "properties": {
                    "conversation_id": {"type": "string", "format": "uuid"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 100},
                },
            },
        ),
        types.Tool(
            name="plan_whatsapp_message",
            description=(
                "Preview or save a prewritten, human, or AI-suggested reply. Without a date it "
                "remains a draft; with a date it is scheduled. confirm must be true to persist it."
            ),
            inputSchema={
                "type": "object",
                "required": ["conversation_id", "text", "source", "confirm"],
                "properties": {
                    "conversation_id": {"type": "string", "format": "uuid"},
                    "text": {"type": "string", "minLength": 1, "maxLength": 4000},
                    "source": {"type": "string", "enum": ["prewritten", "ai_suggested", "human"]},
                    "scheduled_at": {"type": "string", "format": "date-time"},
                    "confirm": {"type": "boolean"},
                },
            },
        ),
        types.Tool(
            name="update_planned_whatsapp_message",
            description="Approve/schedule or cancel a WhatsApp draft. confirm must be true.",
            inputSchema={
                "type": "object",
                "required": ["message_id", "action", "confirm"],
                "properties": {
                    "message_id": {"type": "string", "format": "uuid"},
                    "action": {"type": "string", "enum": ["approve", "cancel"]},
                    "scheduled_at": {"type": "string", "format": "date-time"},
                    "confirm": {"type": "boolean", "const": True},
                },
            },
        ),
        types.Tool(
            name="set_whatsapp_conversation_mode",
            description=(
                "Choose automatic onboarding, AI-agent, human, or paused mode. "
                "Automatic onboarding "
                "is strictly capped at two messages."
            ),
            inputSchema={
                "type": "object",
                "required": ["conversation_id", "mode", "confirm"],
                "properties": {
                    "conversation_id": {"type": "string", "format": "uuid"},
                    "mode": {"type": "string", "enum": ["automatic", "ai", "human", "paused"]},
                    "automatic_message_limit": {"type": "integer", "minimum": 0, "maximum": 2},
                    "confirm": {"type": "boolean", "const": True},
                },
            },
        ),
        types.Tool(
            name="reply_to_incoming_message",
            description=(
                "Atomically stop pending automatic replies, switch the conversation to AI, send "
                "one concise personalized WhatsApp reply and mark the incoming message as read "
                "after success. A second reply to the same incoming message is rejected."
            ),
            inputSchema={
                "type": "object",
                "required": ["message_id", "text", "confirm_send"],
                "properties": {
                    "message_id": {"type": "string", "format": "uuid"},
                    "text": {"type": "string", "minLength": 1, "maxLength": 240},
                    "confirm_send": {"type": "boolean", "const": True},
                    "acknowledge": {"type": "boolean", "default": True},
                },
            },
        ),
        types.Tool(
            name="send_email_message",
            description=(
                "Send one direct email test message to an explicit email address. "
                "This bypasses campaign targeting and does not mark any company as contacted."
            ),
            inputSchema={
                "type": "object",
                "required": ["email", "subject", "text", "confirm_send"],
                "properties": {
                    "email": {"type": "string", "format": "email", "maxLength": 320},
                    "subject": {"type": "string", "minLength": 1, "maxLength": 500},
                    "text": {"type": "string", "minLength": 1, "maxLength": 10000},
                    "confirm_send": {"type": "boolean", "const": True},
                },
            },
        ),
        types.Tool(
            name="list_contact_history",
            description=(
                "List every attempted contact together with the cached company information."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "channel": {"type": "string", "enum": ["all", "email", "whatsapp"]},
                    "source_type": SOURCE_OR_ALL_SCHEMA,
                    "status": {
                        "type": "string",
                        "enum": ["all", "pending", "sent", "failed"],
                        "default": "sent",
                    },
                    "limit": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 100},
                },
            },
        ),
        types.Tool(
            name="list_incoming_messages",
            description=(
                "Read WhatsApp messages received through Evolution API or WAHA. "
                "Unread messages are returned by default."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "unread_only": {"type": "boolean", "default": True},
                    "sender_phone": {"type": "string", "maxLength": 80},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
                },
            },
        ),
        types.Tool(
            name="configure_incoming_webhook",
            description=(
                "Reconnect the Evolution API webhook. WAHA webhooks are configured per session."
            ),
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="acknowledge_incoming_messages",
            description="Mark selected incoming WhatsApp messages as read.",
            inputSchema={
                "type": "object",
                "required": ["message_ids"],
                "properties": {
                    "message_ids": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 100,
                        "items": {"type": "string", "format": "uuid"},
                    }
                },
            },
        ),
        types.Tool(
            name="set_do_not_contact",
            description="Block or unblock all future messages for a cached company.",
            inputSchema={
                "type": "object",
                "required": ["company_id", "blocked"],
                "properties": {
                    "company_id": {"type": "string", "format": "uuid"},
                    "blocked": {"type": "boolean"},
                    "notes": {"type": "string", "maxLength": 2000},
                },
            },
        ),
        types.Tool(
            name="list_scrape_runs",
            description="List recent online collection runs and their status.",
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20}
                },
            },
        ),
        types.Tool(
            name="close_browser",
            description="Close all shared Chromium pages.",
            inputSchema={"type": "object", "properties": {}},
        ),
    ]


async def _collect_pages(args: dict[str, Any]) -> dict[str, Any]:
    source_arg = str(args.get("source_type", "companies"))
    sources = list(SOURCE_TYPES) if source_arg == "all" else [validate_source_type(source_arg)]
    start_page = int(args.get("start_page", 1))
    max_pages = min(max(int(args.get("max_pages", 1)), 1), 100)
    page_size = int(args.get("page_size", 100))
    skip_existing = bool(args.get("skip_existing", True))
    response: dict[str, Any] = {"sources": {}, "records_saved": 0}

    for source_type in sources:
        end_page = start_page + max_pages - 1
        saved = (
            await saved_page_numbers(source_type, page_size, start_page, end_page)
            if skip_existing
            else set()
        )
        source_result = {"saved_pages": [], "skipped_pages": sorted(saved), "runs": []}
        for page_number in range(start_page, end_page + 1):
            if page_number in saved:
                continue
            page = await fetch_registry_page(
                source_type,
                page_number=page_number,
                page_size=page_size,
            )
            run = await save_registry_page(page)
            source_result["saved_pages"].append(page_number)
            source_result["runs"].append(run)
            response["records_saved"] += run["records_saved"]
            if page.total_pages and page_number >= page.total_pages:
                break
        response["sources"][source_type] = source_result
    return response


async def _campaign(args: dict[str, Any], *, send: bool) -> dict[str, Any]:
    channel = str(args.get("channel", ""))
    if channel not in {"email", "whatsapp"}:
        raise ValueError("channel must be email or whatsapp")
    filters = _target_filters(args, channel=channel)
    filters["contact_status"] = "uncontacted"
    filters["include_do_not_contact"] = False
    filters["limit"] = min(max(int(args.get("limit", 10)), 1), 50)
    companies = await list_companies(**filters)
    subject_template = _optional_text(args, "subject_template")
    body_template = str(args.get("body_template", "")).strip()
    if not body_template:
        raise ValueError("body_template cannot be empty")

    if not send:
        previews = [
            preview_message(
                company,
                channel,
                subject_template,
                body_template,
                permission_first=channel == "whatsapp",
            )
            for company in companies
        ]
        return {
            "mode": "preview",
            "provider": provider_status()[channel],
            "matched": len(companies),
            "sendable": sum(1 for preview in previews if preview["sendable"]),
            "messages": previews,
        }

    if args.get("confirm_send") is not True:
        raise ValueError("confirm_send must be true before sending")
    results: list[dict[str, Any]] = []
    scheduled_at = (
        await next_whatsapp_campaign_start() if channel == "whatsapp" else datetime.now(UTC)
    )
    for index, company in enumerate(companies):
        try:
            results.append(
                await send_message(
                    company,
                    channel,
                    subject_template,
                    body_template,
                    permission_first=channel == "whatsapp",
                    scheduled_at=scheduled_at if channel == "whatsapp" else None,
                )
            )
        except Exception as exc:
            results.append(
                {
                    "company_id": company["id"],
                    "company_name": company["legal_name"],
                    "status": "skipped_or_failed",
                    "error": str(exc),
                }
            )
        if channel == "whatsapp" and index < len(companies) - 1:
            scheduled_at += timedelta(seconds=campaign_delay_seconds(index))
    return {
        "mode": "send",
        "channel": channel,
        "matched": len(companies),
        "sent": sum(1 for result in results if result.get("status") == "sent"),
        "queued": sum(1 for result in results if result.get("status") == "queued"),
        "not_sent": sum(1 for result in results if result.get("status") not in {"sent", "queued"}),
        "results": results,
    }


@app.call_tool()
async def call_tool(name: str, arguments: dict[str, Any] | None) -> list[types.TextContent]:
    args = arguments or {}
    async with _tool_lock:
        try:
            if name == "health_check":
                database = await database_health()
                return _json_content(
                    {
                        "status": "ok" if database.get("connected") else "degraded",
                        "service": settings.app_name,
                        "version": SERVICE_VERSION,
                        "transport": settings.mcp_transport,
                        "database": database,
                        "repository": (
                            await repository_stats() if database.get("connected") else None
                        ),
                        "browser": browser_status(),
                        "providers": provider_status(),
                    }
                )

            if name == "inspect_registry":
                source_type = validate_source_type(str(args.get("source_type", "")))
                page = await fetch_registry_page(source_type, page_number=1, page_size=20)
                sample_size = min(max(int(args.get("sample_size", 5)), 1), 20)
                return _json_content(
                    {
                        "source_type": source_type,
                        "url": page.url,
                        "title": page.title,
                        "page_number": page.page_number,
                        "total_pages": page.total_pages,
                        "total_records": page.total_records,
                        "visible_rows": len(page.companies),
                        "sample": [item.to_dict() for item in page.companies[:sample_size]],
                    }
                )

            if name == "search_registry":
                source_arg = str(args.get("source_type", "all")).strip().lower()
                sources = (
                    list(SOURCE_TYPES)
                    if source_arg == "all"
                    else [validate_source_type(source_arg)]
                )
                query = str(args.get("query", "")).strip()
                if not query:
                    raise ValueError("query cannot be empty")
                limit = min(max(int(args.get("limit", 20)), 1), 200)
                records_by_source: list[list[dict[str, Any]]] = []
                source_results: dict[str, Any] = {}
                for source_type in sources:
                    page = await fetch_registry_page(
                        source_type, page_number=1, page_size=200, query=query
                    )
                    saved = (
                        await save_registry_page(page, query=query)
                        if bool(args.get("save_results", True))
                        else None
                    )
                    source_records = [item.to_dict() for item in page.companies]
                    source_results[source_type] = {
                        "visible_matches": len(source_records),
                        "saved": saved,
                    }
                    records_by_source.append(source_records)
                records = [
                    source_records[index]
                    for index in range(max(map(len, records_by_source), default=0))
                    for source_records in records_by_source
                    if index < len(source_records)
                ]
                return _json_content(
                    {
                        "source_type": source_arg,
                        "query": query,
                        "visible_matches": len(records),
                        "sources": source_results,
                        "records": records[:limit],
                    }
                )

            if name == "collect_registry_pages":
                return _json_content(await _collect_pages(args))

            if name == "find_saved_targets":
                return _json_content({"targets": await list_companies(**_target_filters(args))})

            if name == "preview_targeted_messages":
                return _json_content(await _campaign(args, send=False))

            if name == "send_targeted_messages":
                return _json_content(await _campaign(args, send=True))

            if name == "send_whatsapp_message":
                if args.get("confirm_send") is not True:
                    raise ValueError("confirm_send must be true before sending")
                recipient = normalize_benin_phone(str(args.get("number", "")))
                if not recipient:
                    raise ValueError("number must be a valid Benin phone number")
                text = str(args.get("text", "")).strip()
                if not text:
                    raise ValueError("text cannot be empty")
                if len(text) > 240:
                    raise ValueError("WhatsApp messages must not exceed 240 characters")
                provider = str(args.get("provider") or settings.whatsapp_provider)
                session_name = str(
                    args.get("session")
                    or (
                        settings.waha_default_session
                        if provider == "waha"
                        else settings.evolution_api_instance
                    )
                )
                message_id = await send_whatsapp(
                    recipient, text, provider=provider, session=session_name
                )
                recorded = await record_whatsapp_outbound(
                    provider=provider,
                    session_name=session_name,
                    phone=recipient,
                    text=text,
                    provider_message_id=message_id,
                    source="human",
                )
                return _json_content(
                    {
                        "status": "sent",
                        "channel": "whatsapp",
                        "recipient": recipient,
                        "provider": provider,
                        "session": session_name,
                        "provider_message_id": message_id,
                        **recorded,
                    }
                )

            if name == "list_whatsapp_sessions":
                return _json_content({"sessions": await list_waha_sessions()})

            if name == "start_fidelapp_sales_sequence":
                if args.get("confirm_send") is not True:
                    raise ValueError("confirm_send must be true")
                recipient = normalize_benin_phone(str(args.get("number", "")))
                if not recipient:
                    raise ValueError("number must be a valid Benin phone number")
                provider = str(args.get("provider") or "waha")
                session_name = str(args.get("session") or settings.waha_default_session)
                return _json_content(
                    await start_fidelapp_sales_sequence(
                        phone=recipient,
                        company_name=str(args.get("company_name", "")).strip(),
                        provider=provider,
                        session_name=session_name,
                    )
                )

            if name == "resume_fidelapp_sales_sequence":
                if args.get("confirm") is not True:
                    raise ValueError("confirm must be true")
                recipient = normalize_benin_phone(str(args.get("number", "")))
                if not recipient:
                    raise ValueError("number must be a valid Benin phone number")
                provider = str(args.get("provider") or "waha")
                session_name = str(args.get("session") or settings.waha_default_session)
                return _json_content(
                    await resume_fidelapp_sales_sequence(
                        phone=recipient,
                        company_name=str(args.get("company_name", "")).strip(),
                        provider=provider,
                        session_name=session_name,
                    )
                )

            if name == "list_whatsapp_conversations":
                conversations = await list_whatsapp_conversations(int(args.get("limit", 50)))
                return _json_content({"count": len(conversations), "conversations": conversations})

            if name == "get_whatsapp_conversation":
                return _json_content(
                    await get_whatsapp_conversation(
                        str(args.get("conversation_id", "")), int(args.get("limit", 100))
                    )
                )

            if name == "plan_whatsapp_message":
                preview = {
                    "conversation_id": str(args.get("conversation_id", "")),
                    "text": str(args.get("text", "")).strip(),
                    "source": str(args.get("source", "")),
                    "scheduled_at": _optional_datetime(args, "scheduled_at"),
                }
                if not preview["text"]:
                    raise ValueError("text cannot be empty")
                if args.get("confirm") is not True:
                    return _json_content({"mode": "preview", **preview})
                return _json_content(
                    await plan_whatsapp_message(
                        conversation_id=preview["conversation_id"],
                        text=preview["text"],
                        source=preview["source"],
                        scheduled_at=preview["scheduled_at"],
                    )
                )

            if name == "update_planned_whatsapp_message":
                if args.get("confirm") is not True:
                    raise ValueError("confirm must be true")
                return _json_content(
                    await update_planned_whatsapp_message(
                        str(args.get("message_id", "")),
                        action=str(args.get("action", "")),
                        scheduled_at=_optional_datetime(args, "scheduled_at"),
                    )
                )

            if name == "set_whatsapp_conversation_mode":
                if args.get("confirm") is not True:
                    raise ValueError("confirm must be true")
                automatic_limit = args.get("automatic_message_limit")
                return _json_content(
                    await set_whatsapp_conversation_mode(
                        str(args.get("conversation_id", "")),
                        mode=str(args.get("mode", "")),
                        automatic_message_limit=(
                            int(automatic_limit) if automatic_limit is not None else None
                        ),
                    )
                )

            if name == "reply_to_incoming_message":
                if args.get("confirm_send") is not True:
                    raise ValueError("confirm_send must be true before sending")
                text = str(args.get("text", "")).strip()
                if not text:
                    raise ValueError("text cannot be empty")
                if len(text) > 240:
                    raise ValueError("WhatsApp replies must not exceed 240 characters")
                reserved = await reserve_incoming_reply(
                    str(args.get("message_id", "")), text
                )
                recipient = normalize_benin_phone(str(reserved.get("recipient") or ""))
                if not recipient:
                    await complete_incoming_reply(
                        reserved["reply_id"],
                        success=False,
                        error_message="Incoming message has no valid sender phone",
                    )
                    raise ValueError("incoming message has no valid sender phone")
                provider = str(reserved.get("provider") or settings.whatsapp_provider)
                session_name = str(reserved.get("session") or "")
                try:
                    message_id = await send_whatsapp(
                        recipient, text, provider=provider, session=session_name
                    )
                except Exception as exc:
                    await complete_incoming_reply(
                        reserved["reply_id"], success=False, error_message=str(exc)
                    )
                    raise
                acknowledged = await complete_incoming_reply(
                    reserved["reply_id"],
                    success=True,
                    provider_message_id=message_id,
                    acknowledge=bool(args.get("acknowledge", True)),
                )
                return _json_content(
                    {
                        "status": "sent",
                        "channel": "whatsapp",
                        "recipient": recipient,
                        "incoming_message_id": reserved["incoming_message_id"],
                        "sender_name": reserved.get("sender_name"),
                        "provider": provider,
                        "session": session_name,
                        "provider_message_id": message_id,
                        "conversation_id": reserved["conversation_id"],
                        "conversation_message_id": reserved["reply_id"],
                        **acknowledged,
                    }
                )

            if name == "send_email_message":
                if args.get("confirm_send") is not True:
                    raise ValueError("confirm_send must be true before sending")
                email = str(args.get("email", "")).strip()
                subject = str(args.get("subject", "")).strip()
                text = str(args.get("text", "")).strip()
                if not email or "@" not in email:
                    raise ValueError("email must be valid")
                if not subject:
                    raise ValueError("subject cannot be empty")
                if not text:
                    raise ValueError("text cannot be empty")
                message_id = await asyncio.to_thread(_send_smtp, email, subject, text)
                return _json_content(
                    {
                        "status": "sent",
                        "channel": "email",
                        "recipient": email,
                        "provider_message_id": message_id,
                    }
                )

            if name == "list_contact_history":
                return _json_content(
                    {
                        "contacts": await list_contact_history(
                            channel=args.get("channel", "all"),
                            source_type=args.get("source_type", "all"),
                            status=args.get("status", "sent"),
                            limit=int(args.get("limit", 100)),
                        )
                    }
                )

            if name == "list_incoming_messages":
                messages = await list_incoming_messages(
                    unread_only=bool(args.get("unread_only", True)),
                    sender_phone=_optional_text(args, "sender_phone"),
                    limit=int(args.get("limit", 50)),
                )
                return _json_content({"count": len(messages), "messages": messages})

            if name == "configure_incoming_webhook":
                return _json_content(
                    {
                        "evolution_api": await configure_evolution_webhook(),
                        "waha": await configure_waha_webhooks(),
                    }
                )

            if name == "acknowledge_incoming_messages":
                message_ids = args.get("message_ids")
                if not isinstance(message_ids, list):
                    raise ValueError("message_ids must be an array")
                return _json_content(
                    await acknowledge_incoming_messages([str(value) for value in message_ids])
                )

            if name == "set_do_not_contact":
                return _json_content(
                    await set_do_not_contact(
                        str(args.get("company_id", "")),
                        bool(args.get("blocked")),
                        _optional_text(args, "notes"),
                    )
                )

            if name == "list_scrape_runs":
                return _json_content({"runs": await list_runs(int(args.get("limit", 20)))})

            if name == "close_browser":
                await stop_browser()
                return _json_content({"success": True, "browser": browser_status()})

            raise ValueError(f"Unknown tool: {name}")
        except Exception as exc:
            log.exception("MCP tool %s failed", name)
            return _json_content(
                {
                    "success": False,
                    "tool": name,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                }
            )


async def _run_stdio() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


async def _send_json(
    send,
    status: int,
    payload: dict[str, Any],
    extra_headers: list[tuple[bytes, bytes]] | None = None,
) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = [
        (b"content-type", b"application/json; charset=utf-8"),
        (b"content-length", str(len(body)).encode("ascii")),
    ]
    headers.extend(extra_headers or [])
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": headers,
        }
    )
    await send({"type": "http.response.body", "body": body})


async def _read_json_body(receive, *, max_bytes: int = 2_000_000) -> dict[str, Any]:
    body = bytearray()
    more_body = True
    while more_body:
        event = await receive()
        if event.get("type") != "http.request":
            continue
        body.extend(event.get("body", b""))
        if len(body) > max_bytes:
            raise ValueError("request body is too large")
        more_body = bool(event.get("more_body", False))
    payload = json.loads(body or b"{}")
    if not isinstance(payload, dict):
        raise ValueError("request body must be a JSON object")
    return payload


def _request_is_authorized(scope: dict[str, Any]) -> bool:
    configured_key = settings.mcp_api_key
    if not configured_key or not configured_key.get_secret_value():
        return settings.app_environment.strip().lower() == "development"

    authorization = next(
        (
            value.decode("latin-1")
            for name, value in scope.get("headers", [])
            if name.lower() == b"authorization"
        ),
        "",
    )
    scheme, separator, token = authorization.partition(" ")
    return bool(
        separator
        and scheme.lower() == "bearer"
        and hmac.compare_digest(token.strip(), configured_key.get_secret_value())
    )


async def _run_sse() -> None:
    import uvicorn
    from mcp.server.sse import SseServerTransport
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

    sse = SseServerTransport("/messages/")
    streamable_http = StreamableHTTPSessionManager(
        app=app,
        json_response=True,
        stateless=True,
    )

    async def asgi_app(scope, receive, send):
        if scope["type"] != "http":
            return
        path = scope.get("path", "")
        method = scope.get("method", "GET")
        if path == "/health" and method == "GET":
            database = await database_health()
            await _send_json(
                send,
                200 if database.get("connected") else 503,
                {
                    "status": "ok" if database.get("connected") else "degraded",
                    "service": settings.app_name,
                    "version": SERVICE_VERSION,
                    "database": database,
                    "providers": provider_status(),
                    "mcp_authentication": {
                        "configured": bool(
                            settings.mcp_api_key and settings.mcp_api_key.get_secret_value()
                        )
                    },
                },
            )
        elif path in {"/webhooks/evolution", "/webhooks/evolution/"} and method == "POST":
            if not webhook_request_is_authorized(scope.get("headers", [])):
                await _send_json(send, 401, {"status": "unauthorized"})
                return
            try:
                payload = await _read_json_body(receive)
                parsed = parse_evolution_message(payload)
                if parsed is None:
                    await _send_json(send, 200, {"status": "ignored"})
                    return
                result = await save_incoming_message(parsed)
                conversation = await advance_incoming_conversation(result, parsed)
                await _send_json(
                    send,
                    200,
                    {"status": "accepted", **result, "conversation": conversation},
                )
            except (json.JSONDecodeError, ValueError) as exc:
                await _send_json(send, 400, {"status": "invalid_request", "error": str(exc)})
            except Exception:
                log.exception("Evolution webhook processing failed")
                await _send_json(send, 500, {"status": "error"})
        elif path in {"/webhooks/waha", "/webhooks/waha/"} and method == "POST":
            if not waha_webhook_request_is_authorized(scope.get("headers", [])):
                await _send_json(send, 401, {"status": "unauthorized"})
                return
            try:
                payload = await _read_json_body(receive)
                parsed = parse_waha_message(payload)
                if parsed is None:
                    await _send_json(send, 200, {"status": "ignored"})
                    return
                result = await save_incoming_message(parsed)
                conversation = await advance_incoming_conversation(result, parsed)
                await _send_json(
                    send,
                    200,
                    {"status": "accepted", **result, "conversation": conversation},
                )
            except (json.JSONDecodeError, ValueError) as exc:
                await _send_json(send, 400, {"status": "invalid_request", "error": str(exc)})
            except Exception:
                log.exception("WAHA webhook processing failed")
                await _send_json(send, 500, {"status": "error"})
        elif path in {"/mcp", "/mcp/"}:
            if not _request_is_authorized(scope):
                await _send_json(
                    send,
                    401,
                    {"status": "unauthorized"},
                    [(b"www-authenticate", b"Bearer")],
                )
                return
            await streamable_http.handle_request(scope, receive, send)
        elif path == "/sse" and method == "GET":
            if not _request_is_authorized(scope):
                await _send_json(
                    send,
                    401,
                    {"status": "unauthorized"},
                    [(b"www-authenticate", b"Bearer")],
                )
                return
            async with sse.connect_sse(scope, receive, send) as streams:
                await app.run(streams[0], streams[1], app.create_initialization_options())
        elif path.startswith("/messages/") and method == "POST":
            if not _request_is_authorized(scope):
                await _send_json(
                    send,
                    401,
                    {"status": "unauthorized"},
                    [(b"www-authenticate", b"Bearer")],
                )
                return
            await sse.handle_post_message(scope, receive, send)
        else:
            await _send_json(send, 404, {"status": "not_found"})

    config = uvicorn.Config(
        asgi_app,
        host=settings.mcp_host,
        port=settings.mcp_port,
        log_level=settings.log_level.lower(),
    )
    async with streamable_http.run():
        try:
            webhook = await configure_evolution_webhook()
            if webhook.get("configured"):
                log.info("Evolution inbound webhook configured for %s", webhook["url"])
            else:
                log.warning("Evolution inbound webhook is inactive: %s", webhook.get("reason"))
        except Exception as exc:
            log.warning("Could not configure Evolution inbound webhook: %s", exc)
        try:
            webhook = await configure_waha_webhooks()
            if webhook.get("configured"):
                log.info("WAHA inbound webhooks configured for %s", webhook["sessions"])
            else:
                log.warning("WAHA inbound webhook is inactive: %s", webhook.get("reason"))
        except Exception as exc:
            log.warning("Could not configure WAHA inbound webhooks: %s", exc)
        dispatcher_task = asyncio.create_task(run_outbound_dispatcher())
        poller_task = (
            asyncio.create_task(run_waha_inbound_poller())
            if settings.waha_polling_enabled
            else None
        )
        try:
            await uvicorn.Server(config).serve()
        finally:
            dispatcher_task.cancel()
            tasks = [dispatcher_task]
            if poller_task:
                poller_task.cancel()
                tasks.append(poller_task)
            await asyncio.gather(*tasks, return_exceptions=True)


async def main() -> None:
    try:
        if settings.mcp_transport == "sse":
            log.info("Starting MCP SSE server on %s:%s", settings.mcp_host, settings.mcp_port)
            await _run_sse()
        else:
            await _run_stdio()
    finally:
        await stop_browser()
        await close_database()


def run() -> None:
    configure_logging()
    asyncio.run(main())


if __name__ == "__main__":
    run()
