from pydantic import SecretStr

from enterprise_message_bot import mcp_server


def test_mcp_bearer_authentication(monkeypatch) -> None:
    monkeypatch.setattr(mcp_server.settings, "app_environment", "production")
    monkeypatch.setattr(mcp_server.settings, "mcp_api_key", SecretStr("expected-secret"))

    assert mcp_server._request_is_authorized(
        {"headers": [(b"authorization", b"Bearer expected-secret")]}
    )
    assert not mcp_server._request_is_authorized(
        {"headers": [(b"authorization", b"Bearer wrong-secret")]}
    )
    assert not mcp_server._request_is_authorized({"headers": []})
