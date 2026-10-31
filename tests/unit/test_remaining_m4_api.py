from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dbic.adapters.agent.factory import DatabaseAgentRuntime
from dbic.adapters.registry import YamlRegistry
from dbic.api.service import DiagnosticService
from dbic.core.models import ToolResult, utc_now
from dbic.core.policy import DATABASE_TOOLS
from dbic.core.runner import TaskRunner
from dbic.main import create_app
from tests.fakes import FakeStore
from tests.unit.test_api import settings
from tests.unit.test_mcp import HEADERS, rpc
from tests.unit.test_redis_api import ForbiddenAgent


class Tools:
    def __init__(self):
        self.calls = []

    async def run(self, *, tool_id, instance_id, params):
        self.calls.append(tool_id)
        assert not params
        payload = {
            "postgresql.blocking_sessions": {"sessions": [], "truncated": False},
            "postgresql.vacuum_health": {"tables": [], "truncated": False, "stats_reset_at": None},
            "doris.fe_be_health": {"online_backends": 1, "total_backends": 1},
            "doris.query_profile_summary": {"samples": [], "truncated": False},
        }[tool_id]
        return ToolResult(
            source=tool_id,
            observed_at=utc_now(),
            duration_ms=1,
            status="ok" if tool_id.endswith("fe_be_health") else "empty",
            payload=payload,
            limitations=["bounded current lab snapshot"],
        )


@pytest.mark.parametrize(
    "kind,problem", [("postgresql", "maintenance_health"), ("doris", "cluster_health")]
)
@pytest.mark.parametrize("enabled", [True, False])
def test_rest_mcp_policy_and_model_isolation(kind, problem, enabled):
    registry = YamlRegistry(Path("configs/instances.yaml"), **{kind + "_enabled": enabled})
    store, tools = FakeStore(), Tools()
    runner = TaskRunner(registry, tools, store, DatabaseAgentRuntime(ForbiddenAgent()))
    app = create_app(
        settings().model_copy(update={"mcp_enabled": True}),
        readiness=lambda: True,
        diagnostics=DiagnosticService(registry, store, runner),
    )
    body = {"instance_id": kind + "-lab-01", "problem_type": problem}
    with TestClient(app, base_url="http://127.0.0.1") as client:
        result = client.post("/api/v1/diagnoses", headers=HEADERS, json=body)
        if not enabled:
            assert result.status_code == 403 and not tools.calls
            return
        assert result.status_code == 202
        path = "/api/v1/diagnoses/" + result.json()["task_id"]
        detail = client.get(path, headers=HEADERS).json()
        evidence = client.get(path + "/evidence", headers=HEADERS).json()
        assert len(evidence) == len(tools.calls) == 2
        assert detail["task"]["status"] == "partial"
        assert detail["report"]["agent_status"] == "disabled"
        assert len(detail["report"]["findings"]) == 2
        assert (
            client.post(
                path + "/feedback", headers=HEADERS, json={"verdict": "correct"}
            ).status_code
            == 201
        )
        for source in DATABASE_TOOLS[kind]:
            name = source.replace(".", "_")
            result = rpc(
                client,
                "tools/call",
                {"name": name, "arguments": {"instance_id": body["instance_id"]}},
            ).json()["result"]
            assert not result.get("isError")
            item = result["structuredContent"]
            assert client.get(
                "/api/v1/diagnoses/" + item["task_id"] + "/evidence", headers=HEADERS
            ).json() == [item]
            assert rpc(
                client,
                "tools/call",
                {"name": name, "arguments": {"instance_id": body["instance_id"], "sql": "private"}},
            ).json()["result"]["isError"]
        for update in (
            {"problem_type": "lock_wait"},
            {"sql": "private"},
            {"window": {"start": "2020-01-01T00:00:00Z", "end": "2020-01-01T00:01:00Z"}},
        ):
            assert (
                client.post("/api/v1/diagnoses", headers=HEADERS, json=body | update).status_code
                == 422
            )
