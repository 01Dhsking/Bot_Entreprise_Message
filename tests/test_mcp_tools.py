from enterprise_message_bot.mcp_server import list_tools


async def test_reply_to_incoming_message_tool_is_exposed() -> None:
    tools = {tool.name: tool for tool in await list_tools()}

    tool = tools["reply_to_incoming_message"]
    assert "message_id" in tool.inputSchema["required"]
    assert "text" in tool.inputSchema["required"]
    assert tool.inputSchema["properties"]["acknowledge"]["default"] is True
