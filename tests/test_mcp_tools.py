import json

from enterprise_message_bot import mcp_server
from enterprise_message_bot.mcp_server import call_tool, list_tools


async def test_reply_to_incoming_message_tool_is_exposed() -> None:
    tools = {tool.name: tool for tool in await list_tools()}

    tool = tools["reply_to_incoming_message"]
    assert "message_id" in tool.inputSchema["required"]
    assert "text" in tool.inputSchema["required"]
    assert tool.inputSchema["properties"]["acknowledge"]["default"] is True


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

    async def fake_incoming(_message_id):
        return {
            "id": "60e8547c-cf47-48dd-91d0-7e85fcb3ef37",
            "provider": "waha",
            "instance": "commercial-2",
            "sender_phone": "22997000000",
            "sender_name": "Awa",
            "company": {},
        }

    async def fake_send(recipient, text, *, provider, session):
        captured.update(recipient=recipient, text=text, provider=provider, session=session)
        return "waha-message-id"

    async def fake_record(**_kwargs):
        return {"conversation_id": "conversation-id"}

    async def fake_acknowledge(_message_ids):
        return {"acknowledged": 1}

    monkeypatch.setattr(mcp_server, "get_incoming_message", fake_incoming)
    monkeypatch.setattr(mcp_server, "send_whatsapp", fake_send)
    monkeypatch.setattr(mcp_server, "record_whatsapp_outbound", fake_record)
    monkeypatch.setattr(mcp_server, "acknowledge_incoming_messages", fake_acknowledge)

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
