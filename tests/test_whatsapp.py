import base64

from pydantic import SecretStr

from enterprise_message_bot import whatsapp


async def test_send_waha_file_uses_send_file_endpoint(monkeypatch, tmp_path) -> None:
    presentation = tmp_path / "presentation.pptx"
    presentation.write_bytes(b"pptx-content")
    captured = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self):
            return {"id": "WAHA-FILE-ID"}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, endpoint, *, headers, content):
            import json

            captured.update(endpoint=endpoint, headers=headers, payload=json.loads(content))
            return FakeResponse()

    monkeypatch.setattr(whatsapp.settings, "waha_api_base_url", "https://waha.example.com")
    monkeypatch.setattr(whatsapp.settings, "waha_api_key", SecretStr("secret"))
    monkeypatch.setattr(whatsapp.httpx, "AsyncClient", lambda **_kwargs: FakeClient())

    message_id = await whatsapp.send_whatsapp_file(
        "22994482118",
        presentation,
        caption="Voici FidelApp",
        session="commercial-1",
    )

    assert message_id == "WAHA-FILE-ID"
    assert captured["endpoint"] == "https://waha.example.com/api/sendFile"
    assert captured["payload"]["session"] == "commercial-1"
    assert captured["payload"]["chatId"] == "22994482118@c.us"
    assert captured["payload"]["caption"] == "Voici FidelApp"
    assert captured["payload"]["file"]["filename"] == "presentation.pptx"
    assert captured["payload"]["file"]["data"] == base64.b64encode(b"pptx-content").decode("ascii")
