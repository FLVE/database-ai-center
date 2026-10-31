from fastapi.testclient import TestClient

from dbic.core.models import Instance
from dbic.main import create_app
from tests.fakes import FakeTools
from tests.unit.test_api import TOKEN, settings
from tests.unit.test_diagnoses import HEADERS, client_for


def test_public_shell_and_assets_never_contain_credentials():
    with TestClient(create_app(settings(), readiness=lambda: True)) as client:
        for path in ("/", "/assets/styles.css", "/assets/app.js", "/assets/ui.js"):
            response = client.get(path)
            assert response.status_code == 200
            assert TOKEN not in response.text
            assert response.headers["Cache-Control"] == "no-store"
            assert response.headers["X-Content-Type-Options"] == "nosniff"
            assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
        for path in ("/assets/main.py", "/assets/.env", "/assets/../settings.py"):
            assert client.get(path).status_code == 404


def test_instance_summary_auth_policy_and_openapi():
    with TestClient(create_app(settings(), readiness=lambda: True)) as client:
        for headers in ({}, {"Authorization": "Bearer wrong"}):
            assert client.get("/api/v1/instances", headers=headers).status_code == 401
        response = client.get("/api/v1/instances", headers=HEADERS)
        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "no-store"
        items = response.json()
        assert len(items) == 5
        assert items[0] == {
            "id": "mysql-lab-01",
            "database_type": "mysql",
            "environment": "lab",
            "version": "8.4",
            "enabled": True,
            "problem_types": ["lock_wait", "slow_query"],
            "supports_history": True,
        }
        assert all(not item["enabled"] and not item["problem_types"] for item in items[1:])
        assert "connection_ref" not in response.text and "DSN" not in response.text
        schema = client.get("/openapi.json", headers=HEADERS).json()
        assert schema["paths"]["/api/v1/instances"]["get"]["security"] == [{"HTTPBearer": []}]
        assert "/" not in schema["paths"]


def test_instance_summaries_follow_policy_and_playbooks_without_probing_database():
    tools = FakeTools()
    client, _ = client_for(tools)
    with client:
        registry = client.app.state.diagnostics.registry
        expected = {
            "redis": ["memory_pressure"],
            "mongodb": ["activity_health"],
            "postgresql": ["maintenance_health"],
            "doris": ["cluster_health"],
        }
        for kind in expected:
            item = registry.instances[f"{kind}-lab-01"]
            registry.instances[item.id] = item.model_copy(update={"enabled": True})
        items = client.get("/api/v1/instances", headers=HEADERS).json()
        for item in items[1:]:
            assert item["enabled"]
            assert item["problem_types"] == expected[item["database_type"]]
            assert not item["supports_history"]
        registry.instances["mysql-lab-01"] = registry.instances["mysql-lab-01"].model_copy(
            update={"version": "unsupported"}
        )
        registry.instances["other-lab"] = Instance(
            id="other-lab", type="mysql", version="8.4", connection_ref="env:PRIVATE_DSN"
        )
        items = client.get("/api/v1/instances", headers=HEADERS).json()
        assert len(items) == 5
        assert not items[0]["enabled"] and not items[0]["problem_types"]
        assert not tools.calls
        del registry.instances["mysql-lab-01"]
        assert len(client.get("/api/v1/instances", headers=HEADERS).json()) == 4
