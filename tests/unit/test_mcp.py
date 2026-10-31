import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dbic.adapters.agent.null import NullAgentRuntime
from dbic.adapters.registry import YamlRegistry
from dbic.api.service import DiagnosticService
from dbic.core.runner import TaskRunner
from dbic.main import create_app
from tests.fakes import FakeStore, FakeTools
from tests.unit.test_api import TOKEN, settings

HEADERS = {"Authorization": f"Bearer {TOKEN}", "Accept": "application/json, text/event-stream"}


def setup_mcp(*, enabled=True, max_calls=8, tools=None):
    registry = YamlRegistry(Path("configs/instances.yaml"))
    store, tools = FakeStore(), tools or FakeTools()
    service = DiagnosticService(
        registry, store, TaskRunner(registry, tools, store, NullAgentRuntime(), max_calls=max_calls)
    )
    config = settings().model_copy(update={"mcp_enabled": enabled})
    app = create_app(config, readiness=lambda: True, diagnostics=service)
    return app, store, tools


def rpc(client, method, params=None, headers=None):
    return client.post(
        "/mcp/",
        headers=HEADERS if headers is None else headers,
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
    )


def test_mcp_disabled_and_auth_covers_all_methods():
    app, store, tools = setup_mcp(enabled=False)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert rpc(client, "tools/list").status_code == 404
    app, store, tools = setup_mcp()
    with TestClient(app, base_url="http://127.0.0.1") as client:
        for headers in ({}, {"Authorization": "Bearer wrong"}):
            for path in ("/mcp", "/mcp/"):
                for method in ("GET", "POST", "DELETE", "OPTIONS"):
                    assert client.request(method, path, headers=headers).status_code == 401
        assert not tools.calls and not store.tasks


def test_mcp_handshake_tools_and_rest_evidence_readback():
    app, store, tools = setup_mcp()
    with TestClient(app, base_url="http://127.0.0.1") as client:
        result = rpc(
            client,
            "initialize",
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1"},
            },
        )
        assert result.status_code == 200, result.text
        assert result.json()["result"]["serverInfo"]["name"] == "Database AI Center"
        listed = rpc(client, "tools/list")
        assert listed.status_code == 200, listed.text
        definitions = listed.json()["result"]["tools"]
        assert {t["name"] for t in definitions} == {
            "mysql_health",
            "mysql_lock_chain",
            "mysql_slow_query_summary",
        }
        assert all(t["inputSchema"]["additionalProperties"] is False for t in definitions)
        for name in ["mysql_health", "mysql_lock_chain", "mysql_slow_query_summary"]:
            response = rpc(
                client, "tools/call", {"name": name, "arguments": {"instance_id": "mysql-lab-01"}}
            )
            assert response.status_code == 200, response.text
            result = response.json()["result"]
            assert not result.get("isError"), result
            item = result["structuredContent"]
            evidence = client.get(f"/api/v1/diagnoses/{item['task_id']}/evidence", headers=HEADERS)
            assert evidence.json()[0]["evidence_id"] == item["evidence_id"]
            task = client.get(f"/api/v1/diagnoses/{item['task_id']}", headers=HEADERS).json()
            assert task["task"]["requested_by"] == "mcp-local-user"
            assert len(task["steps"]) == 1
    assert tools.calls == ["mysql.health", "mysql.lock_chain", "mysql.slow_query_summary"]


@pytest.mark.parametrize(
    "name,args",
    [
        ("execute_sql", {"instance_id": "mysql-lab-01"}),
        ("mysql_health", {"instance_id": "production"}),
        ("mysql_health", {"instance_id": "mysql-lab-01", "sql": "DROP TABLE accounts"}),
        ("mysql_health", {"instance_id": "mysql-lab-01", "dsn": "mysql://root:secret@prod"}),
        ("mysql_health", {}),
    ],
)
def test_mcp_rejects_invalid_inputs_without_tool_calls(name, args):
    app, store, tools = setup_mcp()
    with TestClient(app, base_url="http://127.0.0.1") as client:
        result = rpc(client, "tools/call", {"name": name, "arguments": args}).json()
        assert "error" in result or result["result"]["isError"]
        assert "secret" not in json.dumps(result)
        assert not tools.calls and not store.tasks


def test_mcp_host_origin_and_size_limits():
    app, store, tools = setup_mcp()
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert (
            rpc(client, "tools/list", headers=HEADERS | {"Host": "evil.example"}).status_code == 421
        )
        assert (
            rpc(
                client, "tools/list", headers=HEADERS | {"Origin": "https://evil.example"}
            ).status_code
            == 403
        )
        assert rpc(client, "tools/call", {"junk": "x" * 17000}).status_code == 413
    assert not tools.calls


def test_mcp_budget_denial_is_persisted_and_disabled_instance_hidden():
    app, store, tools = setup_mcp(max_calls=0)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        result = rpc(
            client,
            "tools/call",
            {"name": "mysql_health", "arguments": {"instance_id": "mysql-lab-01"}},
        ).json()
        assert result["result"]["isError"]
        assert not tools.calls
        assert next(iter(store.tasks.values())).status == "failed"
        registry = app.state.diagnostics.registry
        registry.instances["mysql-lab-01"] = registry.instances["mysql-lab-01"].model_copy(
            update={"enabled": False}
        )
        assert rpc(client, "tools/list").json()["result"]["tools"] == []


def test_mcp_does_not_return_success_when_report_commit_fails(caplog):
    app, store, tools = setup_mcp()

    async def fail_commit(task, report):
        raise RuntimeError("private-storage-password")

    store.finish_task = fail_commit
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = rpc(
            client,
            "tools/call",
            {"name": "mysql_health", "arguments": {"instance_id": "mysql-lab-01"}},
        )
        assert response.json()["result"]["isError"]
        assert "private-storage-password" not in response.text + caplog.text
        assert tools.calls == ["mysql.health"]
        assert next(iter(store.tasks.values())).status == "running"
