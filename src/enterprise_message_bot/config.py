from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "investbenin-enterprise-agent"
    app_environment: str = "development"
    log_level: str = "INFO"

    companies_registry_url: str = (
        "https://gdb.guichet.investbenin.bj/#/app/data/pm/latest/view/3/latest"
    )
    establishments_registry_url: str = (
        "https://gdb.monentreprise.bj/#/app/data/registre%20pp/latest/view/68/latest"
    )
    companies_data_api_url: str = "https://gdb.guichet.investbenin.bj/data-view/pm/latest/5"
    establishments_data_api_url: str = (
        "https://gdb.monentreprise.bj/data-view/registre%20pp/latest/310"
    )
    navigation_timeout_seconds: float = Field(default=35.0, ge=5, le=180)
    page_settle_seconds: float = Field(default=2.0, ge=0.2, le=30)
    action_delay_seconds: float = Field(default=1.0, ge=0.5, le=30)

    headless: bool = True
    chrome_path: str | None = None
    browser_data_dir: Path = PROJECT_ROOT / "data" / "browser-profile"

    database_url: str = ""
    database_host: str = "localhost"
    database_port: int = Field(default=5432, ge=1, le=65535)
    database_name: str = "enterprise_bot"
    database_user: str = "enterprise_bot"
    database_password: SecretStr | None = None
    database_pool_size: int = Field(default=5, ge=1, le=30)
    database_max_overflow: int = Field(default=5, ge=0, le=30)

    mcp_transport: str = "stdio"
    mcp_host: str = "0.0.0.0"
    mcp_port: int = Field(default=8283, ge=1, le=65535)
    mcp_api_key: SecretStr | None = None

    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_username: str = "solvexsolution.org@gmail.com"
    smtp_password: SecretStr | None = None
    smtp_from_email: str = "solvexsolution.org@gmail.com"
    smtp_from_name: str = "Solvex Solution"
    smtp_use_starttls: bool = True

    whatsapp_provider: str = "disabled"
    evolution_api_base_url: str | None = None
    evolution_api_key: SecretStr | None = None
    evolution_api_instance: str = "Solvexsolution"
    evolution_api_delay_ms: int = Field(default=123, ge=0, le=60_000)
    evolution_api_link_preview: bool = True
    phone_country_code: str = "229"
    phone_national_prefix: str = "01"

    @field_validator("mcp_transport")
    @classmethod
    def validate_transport(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"stdio", "sse"}:
            raise ValueError("MCP_TRANSPORT must be 'stdio' or 'sse'")
        return normalized

    @field_validator("whatsapp_provider")
    @classmethod
    def validate_whatsapp_provider(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"disabled", "evolution_api"}:
            raise ValueError("WHATSAPP_PROVIDER must be 'disabled' or 'evolution_api'")
        return normalized

    @field_validator("database_url", mode="before")
    @classmethod
    def normalize_database_url(cls, value: object) -> str:
        if value is None or not str(value).strip():
            return ""
        value = str(value).strip()
        if value.startswith("postgres://"):
            return value.replace("postgres://", "postgresql+asyncpg://", 1)
        if value.startswith("postgresql://"):
            return value.replace("postgresql://", "postgresql+asyncpg://", 1)
        return value

    @model_validator(mode="after")
    def build_database_url(self) -> "Settings":
        if self.database_url:
            return self

        password = self.database_password.get_secret_value() if self.database_password else ""
        if not password:
            if self.app_environment.strip().lower() != "development":
                raise ValueError("DATABASE_PASSWORD is required outside development")
            password = "enterprise_bot"

        self.database_url = URL.create(
            drivername="postgresql+asyncpg",
            username=self.database_user,
            password=password,
            host=self.database_host,
            port=self.database_port,
            database=self.database_name,
        ).render_as_string(hide_password=False)
        return self

    @property
    def sync_database_url(self) -> str:
        return self.database_url.replace("postgresql+asyncpg://", "postgresql://", 1)

    def registry_url_for(self, source_type: str) -> str:
        if source_type == "companies":
            return self.companies_registry_url
        if source_type == "establishments":
            return self.establishments_registry_url
        raise ValueError("source_type must be 'companies' or 'establishments'")

    def data_api_url_for(self, source_type: str) -> str:
        if source_type == "companies":
            return self.companies_data_api_url
        if source_type == "establishments":
            return self.establishments_data_api_url
        raise ValueError("source_type must be 'companies' or 'establishments'")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
