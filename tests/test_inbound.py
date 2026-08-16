from datetime import UTC, datetime

from pydantic import SecretStr

from enterprise_message_bot import inbound
from enterprise_message_bot.inbound import parse_evolution_message, webhook_request_is_authorized


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
    assert captured["json"]["webhook"]["headers"] == {
        "Authorization": "Bearer evolution-secret"
    }
