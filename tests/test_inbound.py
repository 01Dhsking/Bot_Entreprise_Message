from datetime import UTC, datetime

from pydantic import SecretStr

from enterprise_message_bot import inbound
from enterprise_message_bot.inbound import (
    parse_evolution_message,
    parse_waha_message,
    parse_waha_stored_message,
    webhook_request_is_authorized,
)


def test_parse_incoming_evolution_text_message() -> None:
    parsed = parse_evolution_message(
        {
            "event": "messages.upsert",
            "instance": "Solvexsolution",
            "data": {
                "key": {
                    "remoteJid": "2290194482118@s.whatsapp.net",
                    "fromMe": False,
                    "id": "ABC123",
                },
                "pushName": "Client Test",
                "message": {"conversation": "Oui, je souhaite une demonstration"},
                "messageTimestamp": 1_786_839_000,
            },
        }
    )

    assert parsed is not None
    assert parsed["provider_message_id"] == "ABC123"
    assert parsed["sender_phone"] == "2290194482118"
    assert parsed["sender_name"] == "Client Test"
    assert parsed["message_type"] == "conversation"
    assert parsed["text"] == "Oui, je souhaite une demonstration"
    assert parsed["received_at"] == datetime.fromtimestamp(1_786_839_000, tz=UTC)


def test_parse_ignores_outgoing_and_group_messages() -> None:
    base = {
        "event": "MESSAGES_UPSERT",
        "data": {
            "key": {"remoteJid": "22900000000@s.whatsapp.net", "fromMe": True, "id": "1"},
            "message": {"conversation": "outgoing"},
        },
    }
    assert parse_evolution_message(base) is None
    base["data"]["key"] = {"remoteJid": "12000@g.us", "fromMe": False, "id": "2"}
    assert parse_evolution_message(base) is None


def test_parse_incoming_waha_message_keeps_session() -> None:
    parsed = parse_waha_message(
        {
            "event": "message",
            "session": "commercial-2",
            "payload": {
                "id": "false_22997000000@c.us_ABC",
                "timestamp": 1_786_839_000,
                "from": "22997000000@c.us",
                "fromMe": False,
                "body": "Je voudrais une demo",
                "pushName": "Awa",
            },
        }
    )
    assert parsed is not None
    assert parsed["provider"] == "waha"
    assert parsed["instance"] == "commercial-2"
    assert parsed["sender_phone"] == "22997000000"
    assert parsed["text"] == "Je voudrais une demo"


def test_parse_stored_waha_message_uses_phone_jid_instead_of_lid() -> None:
    parsed = parse_waha_stored_message(
        {
            "id": "false_123456@lid_ABC",
            "timestamp": 1_786_839_000,
            "from": "123456@lid",
            "fromMe": False,
            "body": "Oui",
            "_data": {"key": {"remoteJidAlt": "22994482118@s.whatsapp.net"}},
        },
        session="default",
        expected_phone="22994482118",
    )

    assert parsed is not None
    assert parsed["sender_phone"] == "22994482118"
    assert parsed["remote_jid"] == "22994482118@c.us"


async def test_waha_poller_ignores_history_before_conversation(monkeypatch) -> None:
    created_at = datetime.fromtimestamp(1_786_839_000, tz=UTC)
    saved = []
    advanced = []

    async def conversations():
        return [
            {
                "id": "conversation-id",
                "session": "default",
                "phone": "22994482118",
                "remote_jid": "22994482118@c.us",
                "created_at": created_at,
            }
        ]

    async def messages(*_args, **_kwargs):
        return [
            {
                "id": "old",
                "timestamp": 1_786_838_999,
                "from": "22994482118@c.us",
                "fromMe": False,
                "body": "Ancien message",
            },
            {
                "id": "outgoing",
                "timestamp": 1_786_839_001,
                "from": "22994482118@c.us",
                "fromMe": True,
                "body": "Notre message",
            },
            {
                "id": "new",
                "timestamp": 1_786_839_002,
                "from": "22994482118@c.us",
                "fromMe": False,
                "body": "Nouvelle réponse",
            },
        ]

    async def save(parsed):
        saved.append(parsed)
        return {"created": True, "conversation_id": "conversation-id"}

    async def advance(result, parsed):
        advanced.append((result, parsed))

    monkeypatch.setattr(inbound, "list_pollable_waha_conversations", conversations)
    monkeypatch.setattr(inbound, "list_waha_chat_messages", messages)
    monkeypatch.setattr(inbound, "save_incoming_message", save)
    monkeypatch.setattr(inbound, "advance_incoming_conversation", advance)

    assert await inbound.poll_waha_incoming_once() == 1
    assert [message["provider_message_id"] for message in saved] == ["new"]
    assert len(advanced) == 1


def test_webhook_authentication(monkeypatch) -> None:
    monkeypatch.setattr(inbound.settings, "app_environment", "production")
    monkeypatch.setattr(inbound.settings, "evolution_webhook_secret", SecretStr("expected"))

    assert webhook_request_is_authorized([(b"authorization", b"Bearer expected")])
    assert webhook_request_is_authorized([(b"x-evolution-webhook-secret", b"expected")])
    assert not webhook_request_is_authorized([(b"authorization", b"Bearer incorrect")])


async def test_configure_webhook_uses_evolution_23_wrapper(monkeypatch) -> None:
    captured = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, endpoint, *, headers, json):
            captured.update(endpoint=endpoint, headers=headers, json=json)
            return FakeResponse()

    monkeypatch.setattr(inbound.settings, "evolution_api_base_url", "https://evo.example.com")
    monkeypatch.setattr(inbound.settings, "evolution_api_key", SecretStr("evolution-secret"))
    monkeypatch.setattr(inbound.settings, "evolution_api_instance", "Solvexsolution")
    monkeypatch.setattr(
        inbound.settings,
        "evolution_webhook_url",
        "https://bot.example.com/webhooks/evolution",
    )
    monkeypatch.setattr(inbound.httpx, "AsyncClient", lambda **_kwargs: FakeClient())

    result = await inbound.configure_evolution_webhook()

    assert result["configured"] is True
    assert captured["json"]["webhook"]["events"] == ["MESSAGES_UPSERT"]
    assert captured["json"]["webhook"]["headers"] == {"Authorization": "Bearer evolution-secret"}
