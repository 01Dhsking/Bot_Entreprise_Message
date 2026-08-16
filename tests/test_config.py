from pydantic import SecretStr
from sqlalchemy.engine import make_url

from enterprise_message_bot.config import Settings


def test_postgres_url_is_normalized_for_asyncpg() -> None:
    settings = Settings(
        _env_file=None,
        database_url="postgresql://user:password@localhost:5432/database",
    )
    assert settings.database_url.startswith("postgresql+asyncpg://")
    assert settings.sync_database_url.startswith("postgresql://")


def test_mcp_transport_is_normalized() -> None:
    settings = Settings(_env_file=None, mcp_transport="SSE")
    assert settings.mcp_transport == "sse"


def test_evolution_api_provider_is_normalized() -> None:
    settings = Settings(_env_file=None, whatsapp_provider="Evolution_API")
    assert settings.whatsapp_provider == "evolution_api"


def test_database_url_safely_encodes_complex_password() -> None:
    password = "p@ss:/#% with spaces"
    settings = Settings(
        _env_file=None,
        app_environment="production",
        database_url="",
        database_host="postgres",
        database_name="enterprise_bot",
        database_user="enterprise_bot",
        database_password=SecretStr(password),
    )

    parsed_url = make_url(settings.database_url)
    assert parsed_url.host == "postgres"
    assert parsed_url.password == password
    assert "%40" in settings.database_url
    assert "%23" in settings.database_url


def test_evolution_key_secures_webhook_by_default() -> None:
    settings = Settings(
        _env_file=None,
        evolution_api_key=SecretStr("evolution-secret"),
        mcp_api_key=SecretStr("mcp-secret"),
    )
    assert settings.resolved_evolution_webhook_secret == "evolution-secret"
