from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from dbic.adapters.agent.null import NullAgentRuntime
from dbic.adapters.registry import YamlRegistry
from dbic.api.service import DiagnosticService
from dbic.core.runner import TaskRunner
from dbic.main import create_app
from tests.fakes import FakeStore, FakeTools
from tests.unit.test_api import TOKEN, settings

HEADERS = {"Authorization": f"Bearer {TOKEN}"}


def client_for(tools, **runner_kwargs):
    registry = YamlRegistry(Path("configs/instances.yaml"))
    store = FakeStore()
    service = DiagnosticService(
        registry, store, TaskRunner(registry, tools, store, NullAgentRuntime(), **runner_kwargs)
    )
    return TestClient(create_app(settings(), readiness=lambda: True, diagnostics=service)), store


@pytest.mark.parametrize(
    "problem,status,payload,limits,terminal,findings",
    [
        (
            "lock_wait",
            "ok",
            {"edges": [{"waiter_session": 2, "blocker_session": 1}]},
            [],
            "completed",
            True,
        ),
        ("lock_wait", "empty", {"edges": []}, [], "completed", True),
        ("lock_wait", "forbidden", {}, ["permission denied"], "partial", False),
        (
            "slow_query",
            "ok",
            {"digests": [{"digest": "abc", "calls": 2}]},
            ["cumulative summary"],
            "partial",
            True,
        ),
        ("slow_query", "unavailable", {}, ["no samples"], "partial", False),
    ],
)
def test_diagnosis_paths(problem, status, payload, limits, terminal, findings):
    client, store = client_for(FakeTools(status, payload, limits))
    with client:
        response = client.post(
            "/api/v1/diagnoses",
            headers=HEADERS,
            json={"instance_id": "mysql-lab-01", "problem_type": problem},
        )
        assert response.status_code == 202
        assert response.json()["status"] == "queued"
        task_id = response.json()["task_id"]
        detail = client.get(f"/api/v1/diagnoses/{task_id}", headers=HEADERS).json()
        assert detail["task"]["request_id"] == response.headers["X-Request-ID"]
        assert detail["task"]["status"] == terminal
        assert bool(detail["report"]["findings"]) == findings
        assert len(detail["steps"]) == 2
        evidence = client.get(f"/api/v1/diagnoses/{task_id}/evidence", headers=HEADERS).json()
        ids = {e["evidence_id"] for e in evidence}
        assert all(e["redacted"] for e in evidence)
        assert all(set(f["evidence_ids"]) <= ids for f in detail["report"]["findings"])
        feedback = client.post(
            f"/api/v1/diagnoses/{task_id}/feedback",
            headers=HEADERS,
            json={"verdict": "partial", "comment": "password=secret mysql://root:secret@host"},
        )
        assert feedback.status_code == 201
        assert "secret" not in feedback.text
        assert client.get(f"/api/v1/diagnoses/{task_id}").status_code == 401
        retry = client.post(
            "/api/v1/diagnoses",
            headers=HEADERS,
            json={
                "instance_id": "mysql-lab-01",
                "problem_type": problem,
                "parent_task_id": task_id,
            },
        )
        assert retry.status_code == 202
        assert retry.json()["task_id"] != task_id


def test_rejected_requests_do_not_run_tools():
    tools = FakeTools()
    client, store = client_for(tools)
    with client:
        for body, expected in [
            ({"instance_id": "production", "problem_type": "lock_wait"}, 403),
            ({"instance_id": "mysql-lab-01", "problem_type": "execute_sql"}, 422),
            (
                {"instance_id": "mysql-lab-01", "problem_type": "lock_wait", "sql": "DROP TABLE t"},
                422,
            ),
            (
                {
                    "instance_id": "mysql-lab-01",
                    "problem_type": "lock_wait",
                    "parent_task_id": str(uuid4()),
                },
                409,
            ),
        ]:
            assert (
                client.post("/api/v1/diagnoses", headers=HEADERS, json=body).status_code == expected
            )
        assert client.get(f"/api/v1/diagnoses/{uuid4()}", headers=HEADERS).status_code == 404
    assert not tools.calls
    assert not store.tasks


def test_tool_exception_and_budget_are_recorded():
    class BrokenTools(FakeTools):
        async def run(self, **kwargs):
            raise RuntimeError("password=private")

    client, store = client_for(BrokenTools())
    with client:
        response = client.post(
            "/api/v1/diagnoses",
            headers=HEADERS,
            json={"instance_id": "mysql-lab-01", "problem_type": "lock_wait"},
        )
        detail = client.get("/api/v1/diagnoses/" + response.json()["task_id"], headers=HEADERS)
        assert detail.json()["task"]["status"] == "partial"
        assert "private" not in detail.text
    client, store = client_for(FakeTools(), max_calls=1)
    with client:
        response = client.post(
            "/api/v1/diagnoses",
            headers=HEADERS,
            json={"instance_id": "mysql-lab-01", "problem_type": "lock_wait"},
        )
        result = client.get(
            "/api/v1/diagnoses/" + response.json()["task_id"], headers=HEADERS
        ).json()
        assert result["task"]["status"] == "failed"


def test_historical_window_is_partial():
    client, store = client_for(FakeTools())
    with client:
        response = client.post(
            "/api/v1/diagnoses",
            headers=HEADERS,
            json={
                "instance_id": "mysql-lab-01",
                "problem_type": "lock_wait",
                "window": {"start": "2020-01-01T00:00:00Z", "end": "2020-01-01T00:01:00Z"},
            },
        )
        result = client.get(
            "/api/v1/diagnoses/" + response.json()["task_id"], headers=HEADERS
        ).json()
        assert result["task"]["status"] == "partial"
        assert "historical" in result["report"]["missing_evidence"][-1]
