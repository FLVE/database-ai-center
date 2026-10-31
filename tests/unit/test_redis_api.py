from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dbic.adapters.agent.factory import DatabaseAgentRuntime
from dbic.adapters.diagnostic_tools import DiagnosticTools
from dbic.adapters.registry import YamlRegistry
from dbic.api.service import DiagnosticService
from dbic.core.models import ToolResult, utc_now
from dbic.core.runner import TaskRunner
from dbic.main import create_app
from tests.fakes import FakeStore, FakeTools
from tests.unit.test_api import settings
from tests.unit.test_mcp import HEADERS, rpc


class RedisTools:
    def __init__(self):
        self.calls = []

    async def run(self, *, tool_id, instance_id, params):
        self.calls.append(tool_id)
        assert instance_id == "redis-lab-01" and not params
        payload, status, limits = {}, "unavailable", ["audit not configured"]
        if tool_id == "redis.memory_breakdown":
            payload = {
                "used_memory_bytes": 95,
                "maxmemory_bytes": 100,
                "mem_not_counted_for_evict_bytes": 0,
            }
            status, limits = "ok", []
        elif tool_id == "redis.slowlog_summary":
            payload = {
                "sample_count": 0,
                "earliest_at": None,
                "latest_at": None,
                "min_duration_us": None,
                "max_duration_us": None,
                "total_duration_us": 0,
                "commands": [],
            }
            status, limits = "empty", ["retained samples only"]
        return ToolResult(
            source=tool_id,
            status=status,
            observed_at=utc_now(),
            duration_ms=1,
            payload=payload,
            limitations=limits,
        )


class ForbiddenAgent:
    async def explain(self, **kwargs):
        raise AssertionError("Redis must not call MySQL model")


def redis_app(enabled=True):
    registry = YamlRegistry(Path("configs/instances.yaml"), redis_enabled=enabled)
    store, redis, mysql = FakeStore(), RedisTools(), FakeTools()
    runner = TaskRunner(
        registry, DiagnosticTools(mysql, redis), store, DatabaseAgentRuntime(ForbiddenAgent())
    )
    app = create_app(
        settings().model_copy(update={"mcp_enabled": True}),
        readiness=lambda: True,
        diagnostics=DiagnosticService(registry, store, runner),
    )
    return app, store, redis, mysql


def test_redis_task_persistence_feedback_and_no_model():
    app, store, redis, _ = redis_app()
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.post(
            "/api/v1/diagnoses",
            headers=HEADERS,
            json={"instance_id": "redis-lab-01", "problem_type": "memory_pressure"},
        )
        assert response.status_code == 202
        path = "/api/v1/diagnoses/" + response.json()["task_id"]
        detail = client.get(path, headers=HEADERS).json()
        evidence = client.get(path + "/evidence", headers=HEADERS).json()
        assert detail["task"]["database_type"] == "redis" and detail["task"]["status"] == "partial"
        assert len(evidence) == len(detail["steps"]) == len(redis.calls) == 3
        assert (
            detail["report"]["agent_status"] == "disabled"
            and not detail["report"]["agent_suggestions"]
        )
        assert detail["report"]["missing_evidence"]
        assert all(
            set(f["evidence_ids"]) <= {e["evidence_id"] for e in evidence}
            for f in detail["report"]["findings"]
        )
        assert (
            client.post(
                path + "/feedback", headers=HEADERS, json={"verdict": "partial"}
            ).status_code
            == 201
        )
        retry = client.post(
            "/api/v1/diagnoses",
            headers=HEADERS,
            json={
                "instance_id": "redis-lab-01",
                "problem_type": "memory_pressure",
                "parent_task_id": response.json()["task_id"],
            },
        )
        assert retry.status_code == 202
        schema = client.get("/openapi.json", headers=HEADERS).json()
        assert (
            "memory_pressure"
            in schema["components"]["schemas"]["CreateDiagnosis"]["properties"]["problem_type"][
                "enum"
            ]
        )


@pytest.mark.parametrize(
    "body,enabled,status",
    [
        ({"instance_id": "mysql-lab-01", "problem_type": "memory_pressure"}, True, 422),
        ({"instance_id": "redis-lab-01", "problem_type": "lock_wait"}, True, 422),
        (
            {
                "instance_id": "redis-lab-01",
                "problem_type": "memory_pressure",
                "window": {"start": "2020-01-01T00:00:00Z", "end": "2020-01-01T00:30:00Z"},
            },
            True,
            422,
        ),
        (
            {"instance_id": "redis-lab-01", "problem_type": "memory_pressure", "command": "KEYS *"},
            True,
            422,
        ),
        (
            {
                "instance_id": "redis-lab-01",
                "problem_type": "memory_pressure",
                "path": "/etc/passwd",
            },
            True,
            422,
        ),
        ({"instance_id": "redis-lab-01", "problem_type": "tool:redis.memory_breakdown"}, True, 422),
        ({"instance_id": "redis-lab-01", "problem_type": "memory_pressure"}, False, 403),
        ({"instance_id": "production", "problem_type": "memory_pressure"}, True, 403),
    ],
)
def test_rejected_requests_never_create_task_or_call_tools(body, enabled, status):
    app, store, redis, mysql = redis_app(enabled)
    with TestClient(app) as client:
        response = client.post("/api/v1/diagnoses", headers=HEADERS, json=body)
        assert response.status_code == status
    assert not store.tasks and not redis.calls and not mysql.calls


def test_mcp_lists_each_instance_independently_and_archives_one_evidence():
    app, store, redis, mysql = redis_app()
    with TestClient(app, base_url="http://127.0.0.1") as client:
        names = {x["name"] for x in rpc(client, "tools/list").json()["result"]["tools"]}
        assert len(names) == 6
        for name in sorted(n for n in names if n.startswith("redis_")):
            result = rpc(
                client, "tools/call", {"name": name, "arguments": {"instance_id": "redis-lab-01"}}
            ).json()["result"]
            assert not result.get("isError"), result
            evidence = result["structuredContent"]
            path = "/api/v1/diagnoses/" + evidence["task_id"]
            assert client.get(path + "/evidence", headers=HEADERS).json() == [evidence]
            assert len(client.get(path, headers=HEADERS).json()["steps"]) == 1
        for name, args in [
            ("redis_memory_breakdown", {"instance_id": "mysql-lab-01"}),
            ("mysql_health", {"instance_id": "redis-lab-01"}),
            ("redis_bigkey_audit_read", {"instance_id": "redis-lab-01", "path": "/etc/passwd"}),
        ]:
            assert rpc(client, "tools/call", {"name": name, "arguments": args}).json()["result"][
                "isError"
            ]
        assert len(redis.calls) == 3 and not mysql.calls
        registry = app.state.diagnostics.registry
        registry.instances["redis-lab-01"] = registry.instances["redis-lab-01"].model_copy(
            update={"enabled": False}
        )
        listed = rpc(client, "tools/list").json()["result"]["tools"]
        assert len(listed) == 3 and all(x["name"].startswith("mysql_") for x in listed)
        capabilities = client.get("/api/v1/capabilities", headers=HEADERS).json()
        assert capabilities["databases"][1]["enabled"] is False
        assert capabilities["agent_database_types"] == ["mysql"]
