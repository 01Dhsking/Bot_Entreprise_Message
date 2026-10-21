import json
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from enterprise_message_bot import mcp_server, repository


def fake_session(monkeypatch, conversation):
    session = SimpleNamespace(
        get=AsyncMock(return_value=conversation),
        commit=AsyncMock(),
        add=MagicMock(),
        execute=AsyncMock(),
        scalar=AsyncMock(),
    )
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=session)
    context.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(repository, "SessionFactory", lambda: context)
    return session


async def test_template_1_tool_routes_fixed_sequence(monkeypatch):
    tools = {t.name: t for t in await mcp_server.list_tools()}
    assert "template_1" in tools
    start = AsyncMock(return_value={"status": "scheduled"})
    monkeypatch.setattr(mcp_server, "start_fidelapp_sales_sequence", start)
    result = await mcp_server.call_tool(
        "template_1",
        {
            "number": "0194482118",
            "company_name": "Magasin Test",
            "confirm_send": True,
        },
    )
    assert json.loads(result[0].text)["status"] == "scheduled"
    assert start.await_args.kwargs["company_name"] == "Magasin Test"


@pytest.mark.parametrize("reply", ["oui", "non", "qui êtes-vous ?"])
async def test_first_reply_queues_only_fixed_second_message(monkeypatch, reply):
    conversation = SimpleNamespace(
        id=uuid.uuid4(),
        mode="automatic",
        company_id=None,
        metadata_json={
            "sequence": "fidelapp_sales",
            "stage": "awaiting_identity_reply",
            "company_name": "Magasin Test",
        },
    )
    session = fake_session(monkeypatch, conversation)
    await repository.advance_fidelapp_sales_sequence(str(conversation.id), reply)
    item = session.add.call_args.args[0]
    assert item.text == repository.fidelapp_identity_message("Magasin Test")
    assert item.metadata_json["stage_after_send"] == "completed"
    session.add.assert_called_once()


async def test_stop_never_queues_second_message(monkeypatch):
    conversation = SimpleNamespace(
        id=uuid.uuid4(),
        mode="automatic",
        company_id=None,
        metadata_json={"sequence": "fidelapp_sales", "stage": "awaiting_identity_reply"},
    )
    session = fake_session(monkeypatch, conversation)
    await repository.advance_fidelapp_sales_sequence(str(conversation.id), "STOP")
    assert conversation.mode == "paused"
    session.add.assert_not_called()


async def test_second_success_hands_over_immediately(monkeypatch):
    conversation = SimpleNamespace(
        mode="automatic",
        automatic_messages_sent=1,
        automatic_message_limit=2,
        metadata_json={"sequence": "fidelapp_sales", "stage": "identity_queued"},
    )
    item = SimpleNamespace(
        source="prewritten",
        conversation_id=uuid.uuid4(),
        metadata_json={"stage_after_send": "completed"},
    )
    session = fake_session(monkeypatch, conversation)
    session.get.side_effect = [item, conversation, conversation]
    await repository.complete_planned_whatsapp_message(str(uuid.uuid4()), success=True)
    assert conversation.mode == "human"
    assert conversation.metadata_json["stage"] == "completed"
    session.get.side_effect = None
    session.get.return_value = conversation
    await repository.advance_fidelapp_sales_sequence(str(item.conversation_id), "oui")
    session.add.assert_not_called()
    assert conversation.mode == "human"


@pytest.mark.parametrize("history", [(1, None, None), (None, 1, None), (None, None, 1)])
async def test_existing_campaign_conversation_or_block_skips(monkeypatch, history):
    session = fake_session(monkeypatch, None)
    session.scalar.side_effect = history
    result = await repository.start_fidelapp_sales_sequence(
        phone="22997000000",
        company_name="Magasin Test",
        provider="waha",
        session_name="default",
    )
    assert result["status"] == "skipped"
    session.add.assert_not_called()


async def test_authorized_test_number_can_restart_without_deleting_history(monkeypatch):
    conversation = SimpleNamespace(id=uuid.uuid4())
    session = fake_session(monkeypatch, conversation)
    session.scalar.side_effect = [1, 1, None, 1]
    get_conversation = AsyncMock(return_value=conversation)
    monkeypatch.setattr(repository, "_get_or_create_conversation", get_conversation)
    result = await repository.start_fidelapp_sales_sequence(
        phone="22994482118",
        company_name="Boutique Test",
        provider="waha",
        session_name="default",
    )
    assert result["status"] == "scheduled"
    assert result["test_recipient"] is True
    assert conversation.mode == "automatic"
    assert conversation.automatic_messages_sent == 0
    session.add.assert_called_once()
    assert session.add.call_args.args[0].text == "Bonjour, suis-je bien chez Boutique Test ?"


async def test_test_number_still_respects_do_not_contact(monkeypatch):
    session = fake_session(monkeypatch, None)
    session.scalar.side_effect = [1, 1, 1]
    result = await repository.start_fidelapp_sales_sequence(
        phone="22994482118",
        company_name="Boutique Test",
        provider="waha",
        session_name="default",
    )
    assert result["reason"] == "do_not_contact"
    session.add.assert_not_called()


async def test_dispatch_paces_template_messages_even_without_campaign_queue(monkeypatch):
    session = fake_session(monkeypatch, None)
    latest = datetime.now(UTC) - timedelta(seconds=1)
    session.scalar.side_effect = [None, 0, latest, 1]
    wait = await repository.outbound_dispatch_wait_seconds()
    expected = repository.campaign_delay_seconds(0, at=latest) - 1
    assert expected - 2 <= wait <= expected


async def test_dispatch_no_history_has_no_delay(monkeypatch):
    session = fake_session(monkeypatch, None)
    session.scalar.side_effect = [None, 0, None, 0]
    assert await repository.outbound_dispatch_wait_seconds() == 0
