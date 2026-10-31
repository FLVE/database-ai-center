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
from dbic.plugins.mongodb.models import replica_snapshot
from tests.fakes import FakeStore, FakeTools
from tests.unit.test_api import settings
from tests.unit.test_mcp import HEADERS, rpc
from tests.unit.test_mongodb import replica_raw
from tests.unit.test_redis_api import ForbiddenAgent


class MongoTools:
    def __init__(self):
        self.calls = []

    async def run(self, *, tool_id, instance_id, params):
        self.calls.append(tool_id)
        assert instance_id == "mongodb-lab-01" and not params
        replica = tool_id == "mongodb.replica_health"
        return ToolResult(
            source=tool_id,
            status="ok" if replica else "empty",
            observed_at=utc_now(),
            duration_ms=1,
            limitations=["single-member" if replica else "bounded snapshot"],
            payload=replica_snapshot(replica_raw()).model_dump(mode="json")
            if replica
            else {"samples": [], "truncated": False},
        )


def mongodb_app(enabled=True):
    registry = YamlRegistry(Path("configs/instances.yaml"), mongodb_enabled=enabled)
    store, mongo, other = FakeStore(), MongoTools(), FakeTools()
    runner = TaskRunner(
        registry,
        DiagnosticTools(other, other, mongo),
        store,
        DatabaseAgentRuntime(ForbiddenAgent()),
    )
    app = create_app(
        settings().model_copy(update={"mcp_enabled": True}),
        readiness=lambda: True,
        diagnostics=DiagnosticService(registry, store, runner),
    )
    return app, store, mongo, other


def test_mongodb_api_report_feedback_parent_and_mcp():
    app, store, mongo, other = mongodb_app()
    with TestClient(app, base_url="http://127.0.0.1") as client:
        created = client.post(
            "/api/v1/diagnoses",
            headers=HEADERS,
            json={"instance_id": "mongodb-lab-01", "problem_type": "activity_health"},
        )
        assert created.status_code == 202
        path = "/api/v1/diagnoses/" + created.json()["task_id"]
        detail = client.get(path, headers=HEADERS).json()
        evidence = client.get(path + "/evidence", headers=HEADERS).json()
        assert (
            detail["task"]["database_type"] == "mongodb" and detail["task"]["status"] == "partial"
        )
        assert len(evidence) == len(detail["steps"]) == len(mongo.calls) == 2
        assert detail["report"]["agent_status"] == "disabled" and not other.calls
        ids = {e["evidence_id"] for e in evidence}
        assert len(detail["report"]["findings"]) == 2
        assert all(
            f["evidence_ids"] and set(f["evidence_ids"]) <= ids
            for f in detail["report"]["findings"]
        )
        assert (
            client.post(
                path + "/feedback", headers=HEADERS, json={"verdict": "correct"}
            ).status_code
            == 201
        )
        assert (
            client.post(
                "/api/v1/diagnoses",
                headers=HEADERS,
                json={
                    "instance_id": "mongodb-lab-01",
                    "problem_type": "activity_health",
                    "parent_task_id": created.json()["task_id"],
                },
            ).status_code
            == 202
        )
        names = {t["name"] for t in rpc(client, "tools/list").json()["result"]["tools"]}
        assert len(names) == 5
        for name in ("mongodb_current_ops_summary", "mongodb_replica_health"):
            result = rpc(
                client, "tools/call", {"name": name, "arguments": {"instance_id": "mongodb-lab-01"}}
            ).json()["result"]
            assert not result.get("isError")
            item = result["structuredContent"]
            assert client.get(
                "/api/v1/diagnoses/" + item["task_id"] + "/evidence", headers=HEADERS
            ).json() == [item]
            assert rpc(
                client,
                "tools/call",
                {"name": name, "arguments": {"instance_id": "mongodb-lab-01", "command": "killOp"}},
            ).json()["result"]["isError"]
        schema = client.get("/openapi.json", headers=HEADERS).json()
        assert (
            "activity_health"
            in schema["components"]["schemas"]["CreateDiagnosis"]["properties"]["problem_type"][
                "enum"
            ]
        )


@pytest.mark.parametrize(
    "body,enabled,status",
    [
        ({"instance_id": "mysql-lab-01", "problem_type": "activity_health"}, True, 422),
        ({"instance_id": "mongodb-lab-01", "problem_type": "lock_wait"}, True, 422),
        (
            {
                "instance_id": "mongodb-lab-01",
                "problem_type": "activity_health",
                "command": "killOp",
            },
            True,
            422,
        ),
        (
            {
                "instance_id": "mongodb-lab-01",
                "problem_type": "activity_health",
                "window": {"start": "2020-01-01T00:00:00Z", "end": "2020-01-01T00:01:00Z"},
            },
            True,
            422,
        ),
        ({"instance_id": "mongodb-lab-01", "problem_type": "activity_health"}, False, 403),
        ({"instance_id": "production", "problem_type": "activity_health"}, True, 403),
    ],
)
def test_rejected_requests_have_zero_side_effects(body, enabled, status):
    app, store, mongo, other = mongodb_app(enabled)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.post("/api/v1/diagnoses", headers=HEADERS, json=body).status_code == status
        if not enabled:
            assert not any(
                t["name"].startswith("mongodb_")
                for t in rpc(client, "tools/list").json()["result"]["tools"]
            )
    assert not store.tasks and not mongo.calls and not other.calls
