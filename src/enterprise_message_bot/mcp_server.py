import asyncio
import json
import logging
from datetime import date
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
from .database import close_database, database_health
from .logging_config import configure_logging
from .outreach import preview_message, provider_status, send_message
from .registry_client import fetch_registry_page
from .repository import (
    list_companies,
    list_contact_history,
    list_runs,
    repository_stats,
    save_registry_page,
    saved_page_numbers,
    set_do_not_contact,
)

settings = get_settings()
app = Server(settings.app_name)
log = logging.getLogger(__name__)
_tool_lock = asyncio.Lock()

SOURCE_SCHEMA = {
    "type": "string",
    "enum": ["companies", "establishments"],
    "description": "companies = societes, establishments = etablissements individuels",
}
SOURCE_OR_ALL_SCHEMA = {"type": "string", "enum": ["companies", "establishments", "all"]}
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
            description="Search online in either the companies or establishments directory.",
            inputSchema={
                "type": "object",
                "required": ["source_type", "query"],
                "properties": {
                    "source_type": SOURCE_SCHEMA,
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
                "Filter cached records by city, district, activity, creation date "
                "and contact status."
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
            preview_message(company, channel, subject_template, body_template)
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
    for company in companies:
        try:
            results.append(await send_message(company, channel, subject_template, body_template))
        except Exception as exc:
            results.append(
                {
                    "company_id": company["id"],
                    "company_name": company["legal_name"],
                    "status": "skipped_or_failed",
                    "error": str(exc),
                }
            )
    return {
        "mode": "send",
        "channel": channel,
        "matched": len(companies),
        "sent": sum(1 for result in results if result.get("status") == "sent"),
        "not_sent": sum(1 for result in results if result.get("status") != "sent"),
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
                source_type = validate_source_type(str(args.get("source_type", "")))
                query = str(args.get("query", "")).strip()
                page = await fetch_registry_page(
                    source_type, page_number=1, page_size=200, query=query
                )
                saved = (
                    await save_registry_page(page, query=query)
                    if bool(args.get("save_results", True))
                    else None
                )
                limit = min(max(int(args.get("limit", 20)), 1), 200)
                return _json_content(
                    {
                        "source_type": source_type,
                        "query": query,
                        "visible_matches": len(page.companies),
                        "saved": saved,
                        "records": [item.to_dict() for item in page.companies[:limit]],
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


async def _send_json(send, status: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json; charset=utf-8"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


async def _run_sse() -> None:
    import uvicorn
    from mcp.server.sse import SseServerTransport

    sse = SseServerTransport("/messages/")

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
                    "database": database,
                    "providers": provider_status(),
                },
            )
        elif path == "/sse" and method == "GET":
            async with sse.connect_sse(scope, receive, send) as streams:
                await app.run(streams[0], streams[1], app.create_initialization_options())
        elif path.startswith("/messages/") and method == "POST":
            await sse.handle_post_message(scope, receive, send)
        else:
            await _send_json(send, 404, {"status": "not_found"})

    config = uvicorn.Config(
        asgi_app,
        host=settings.mcp_host,
        port=settings.mcp_port,
        log_level=settings.log_level.lower(),
    )
    await uvicorn.Server(config).serve()


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
