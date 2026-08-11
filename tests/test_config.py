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
