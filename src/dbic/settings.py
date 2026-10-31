from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL, make_url

from dbic.adapters.http import validate_endpoint


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DBIC_", extra="ignore")
    env: Literal["lab"] = "lab"
    api_token: SecretStr
    database_url: SecretStr | None = None
    pg_password: SecretStr | None = None
    configs_dir: Path = Path("configs")
    mysql_diag_password: SecretStr | None = None
    search_backend: Literal["null", "elastic"] = "null"
    metrics_backend: Literal["null", "prometheus"] = "null"
    elastic_url: str = Field(default="", repr=False)
    elastic_api_key: SecretStr | None = None
    prometheus_url: str = Field(default="", repr=False)
    prometheus_token: SecretStr | None = None
    prometheus_instance_label: str = Field(default="instance", pattern=r"^[a-zA-Z_][a-zA-Z0-9_]*$")
    prometheus_instance_value: str = Field(default="", max_length=256)
    history_ca_file: str | None = None
    agent_backend: Literal["null", "openai_compatible"] = "null"
    llm_base_url: str = Field(default="", repr=False)
    llm_api_key: SecretStr | None = None
    llm_model: str = Field(default="", max_length=128)
    llm_timeout_seconds: float = Field(default=8, gt=0, le=8)
    mcp_enabled: bool = False

    def mysql_dsn(self) -> str | None:
        if not self.mysql_diag_password:
            return None
        return URL.create(
            "mysql+pymysql",
            username="diag",
            password=self.mysql_diag_password.get_secret_value(),
            host="mysql-lab",
            port=3306,
            database="performance_schema",
        ).render_as_string(hide_password=False)

    @model_validator(mode="after")
    def valid(self) -> "Settings":
        if len(self.api_token.get_secret_value()) < 24:
            raise ValueError("DBIC_API_TOKEN must contain at least 24 characters")
        self.postgres_url()
        if self.search_backend == "elastic":
            validate_endpoint(self.elastic_url)
        if self.metrics_backend == "prometheus":
            validate_endpoint(self.prometheus_url)
            if not self.prometheus_instance_value:
                raise ValueError(
                    "DBIC_PROMETHEUS_INSTANCE_VALUE is required for the selected lab target"
                )
        if self.agent_backend == "openai_compatible":
            url = urlsplit(self.llm_base_url)
            if (
                not url.hostname
                or url.username
                or url.password
                or url.query
                or url.fragment
                or url.scheme not in {"http", "https"}
                or (
                    url.scheme == "http"
                    and url.hostname
                    not in {"localhost", "127.0.0.1", "::1", "host.docker.internal"}
                )
                or not self.llm_model.strip()
                or not self.llm_api_key
                or not self.llm_api_key.get_secret_value().strip()
            ):
                raise ValueError(
                    "agent requires explicit model/key and HTTPS (HTTP only for local lab)"
                )
        return self

    def postgres_url(self) -> URL:
        if self.database_url:
            url = make_url(self.database_url.get_secret_value())
        elif self.pg_password and self.pg_password.get_secret_value():
            url = URL.create(
                "postgresql+psycopg",
                username="dbic",
                password=self.pg_password.get_secret_value(),
                host="dbic-postgres",
                port=5432,
                database="dbic",
            )
        else:
            raise ValueError("configure DBIC_DATABASE_URL or DBIC_PG_PASSWORD")
        if (
            url.drivername != "postgresql+psycopg"
            or url.host
            not in {
                "localhost",
                "127.0.0.1",
                "::1",
                "dbic-postgres",
            }
            or url.database not in {"dbic", "dbic_test"}
            or url.query
        ):
            raise ValueError("only the local PostgreSQL lab is allowed; URL options forbidden")
        return url
