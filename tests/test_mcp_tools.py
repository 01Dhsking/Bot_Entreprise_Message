import json

from enterprise_message_bot import mcp_server
from enterprise_message_bot.mcp_server import call_tool, list_tools


async def test_reply_to_incoming_message_tool_is_exposed() -> None:
    tools = {tool.name: tool for tool in await list_tools()}

    tool = tools["reply_to_incoming_message"]
    assert "message_id" in tool.inputSchema["required"]
    assert "text" in tool.inputSchema["required"]
    assert tool.inputSchema["properties"]["acknowledge"]["default"] is True
    assert tool.inputSchema["properties"]["text"]["maxLength"] == 240


async def test_multi_session_conversation_tools_are_exposed() -> None:
    tools = {tool.name: tool for tool in await list_tools()}

    assert "list_whatsapp_sessions" in tools
    assert "resume_fidelapp_sales_sequence" in tools
    assert "list_whatsapp_conversations" in tools
    assert "get_whatsapp_conversation" in tools
    assert "plan_whatsapp_message" in tools
    assert "update_planned_whatsapp_message" in tools
    assert "set_whatsapp_conversation_mode" in tools
    assert tools["send_whatsapp_message"].inputSchema["properties"]["session"]


async def test_registry_search_uses_both_client_sources_by_default() -> None:
    tools = {tool.name: tool for tool in await list_tools()}

    search = tools["search_registry"]
    assert search.inputSchema["required"] == ["query"]
    source = search.inputSchema["properties"]["source_type"]
    assert source["default"] == "all"
    assert set(source["enum"]) == {"companies", "establishments", "all"}


async def test_registry_search_merges_companies_and_establishments(monkeypatch) -> None:
    requested_sources = []

    class FakeRecord:
        def __init__(self, source_type: str) -> None:
            self.source_type = source_type

        def to_dict(self):
            return {"source_type": self.source_type, "legal_name": self.source_type}

    class FakePage:
        def __init__(self, source_type: str) -> None:
            self.companies = [FakeRecord(source_type)]

    async def fake_fetch(source_type, **_kwargs):
        requested_sources.append(source_type)
        return FakePage(source_type)

    monkeypatch.setattr(mcp_server, "fetch_registry_page", fake_fetch)

    result = await call_tool(
        "search_registry",
        {"query": "restaurant", "save_results": False, "limit": 2},
    )

    payload = json.loads(result[0].text)
    assert requested_sources == ["companies", "establishments"]
    assert payload["source_type"] == "all"
    assert payload["visible_matches"] == 2
    assert {record["source_type"] for record in payload["records"]} == {
        "companies",
        "establishments",
    }


async def test_start_fidelapp_sequence_uses_waha_session(monkeypatch) -> None:
    captured = {}

    async def fake_start(**kwargs):
        captured.update(kwargs)
        return {"status": "scheduled", "message_id": "message-id"}

    monkeypatch.setattr(mcp_server, "start_fidelapp_sales_sequence", fake_start)

    result = await call_tool(
        "start_fidelapp_sales_sequence",
        {
            "number": "0194482118",
            "company_name": "Boutique Test",
            "provider": "waha",
            "session": "default",
            "confirm_send": True,
        },
    )

    payload = json.loads(result[0].text)
    assert payload["status"] == "scheduled"
    assert captured == {
        "phone": "22994482118",
        "company_name": "Boutique Test",
        "provider": "waha",
        "session_name": "default",
    }


async def test_reply_uses_provider_and_session_from_incoming_message(monkeypatch) -> None:
    captured = {}

    async def fake_reserve(_message_id, _text):
        return {
            "reply_id": "reply-id",
            "conversation_id": "conversation-id",
            "incoming_message_id": "60e8547c-cf47-48dd-91d0-7e85fcb3ef37",
            "provider": "waha",
            "session": "commercial-2",
            "recipient": "22997000000",
            "sender_name": "Awa",
        }

    async def fake_send(recipient, text, *, provider, session):
        captured.update(recipient=recipient, text=text, provider=provider, session=session)
        return "waha-message-id"

    async def fake_complete(_reply_id, **_kwargs):
        return {"acknowledged": 1}

    monkeypatch.setattr(mcp_server, "reserve_incoming_reply", fake_reserve)
    monkeypatch.setattr(mcp_server, "send_whatsapp", fake_send)
    monkeypatch.setattr(mcp_server, "complete_incoming_reply", fake_complete)

    result = await call_tool(
        "reply_to_incoming_message",
        {
            "message_id": "60e8547c-cf47-48dd-91d0-7e85fcb3ef37",
            "text": "Avec plaisir.",
            "confirm_send": True,
        },
    )

    payload = json.loads(result[0].text)
    assert payload["status"] == "sent"
    assert captured == {
        "recipient": "22997000000",
        "text": "Avec plaisir.",
        "provider": "waha",
        "session": "commercial-2",
    }
