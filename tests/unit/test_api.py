import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from dbic.main import create_app
from dbic.settings import Settings

TOKEN = "unit-test-token-not-a-real-secret"


def settings():
    return Settings(api_token=TOKEN, pg_password="test-only")


@pytest.mark.parametrize("available", [True, False])
def test_health_readiness_and_auth(available):
    with TestClient(create_app(settings(), readiness=lambda: available)) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        response = client.get("/readyz")
        assert response.status_code == (200 if available else 503)
        if not available:
            assert response.json()["code"] == "dependency_unavailable"
            assert response.json()["request_id"] == response.headers["X-Request-ID"]
        for headers in ({}, {"Authorization": "Bearer wrong"}):
            assert client.get("/api/v1/capabilities", headers=headers).status_code == 401
        headers = {"Authorization": f"Bearer {TOKEN}"}
        result = client.get("/api/v1/capabilities", headers=headers).json()
        assert result["milestone"] == "M4"
        assert result["agent_backend"] == "null" and result["mcp_enabled"] is False
        assert result["historical_backends"] == {"metrics": "null", "search": "null"}
        assert len(result["databases"]) == 5
        assert result["databases"][0]["status"] == "available"
        assert all(
            x["status"] == "not_implemented" and x["tools"] == [] for x in result["databases"][2:]
        )
        assert result["databases"][1] == {
            "database_type": "redis",
            "status": "available",
            "enabled": False,
            "tools": [],
        }
        assert client.get("/openapi.json").status_code == 401
        schema = client.get("/openapi.json", headers=headers).json()
        assert "/api/v1/diagnoses" in schema["paths"]
        assert client.post("/api/v1/diagnoses").status_code == 401


@pytest.mark.parametrize(
    "overrides",
    [
        {"env": "production"},
        {"api_token": "short"},
        {"database_url": "postgresql+psycopg://u:p@production/dbic"},
        {"database_url": "postgresql+psycopg://u:p@localhost/dbic?host=production"},
    ],
)
def test_reject_unsafe_settings(overrides):
    with pytest.raises(ValidationError):
        Settings(**({"api_token": TOKEN, "pg_password": "test-only"} | overrides))


def test_password_url_encoding():
    config = Settings(api_token=TOKEN, pg_password="a@b:/#%$")
    assert config.postgres_url().password == "a@b:/#%$"
    assert "a@b:/#%$" not in repr(config)


def test_readiness_database_error_is_sanitized():
    from unittest.mock import Mock

    from sqlalchemy.exc import OperationalError

    from dbic.adapters.persistence.postgres import database_ready

    engine = Mock()
    engine.connect.side_effect = OperationalError("secret DSN", {}, Exception("secret"))
    assert database_ready(engine) is False


def test_internal_error_is_sanitized():
    def failing():
        raise RuntimeError("secret-password")

    with TestClient(
        create_app(settings(), readiness=failing), raise_server_exceptions=False
    ) as client:
        response = client.get("/readyz")
        assert response.status_code == 500
        assert "secret-password" not in response.text
        assert response.headers["X-Request-ID"] == response.json()["request_id"]
