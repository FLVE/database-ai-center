from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from dbic.core.models import DiagnosticTask, TaskStatus
from tests.fakes import FakeTools
from tests.unit.test_diagnoses import HEADERS, client_for

BASE = datetime(2026, 1, 1, tzinfo=UTC)


def history_task(number, *, status="completed", instance="mysql-lab-01", created=BASE):
    return DiagnosticTask(
        task_id=UUID(int=number),
        instance_id=instance,
        database_type="redis" if instance.startswith("redis") else "mysql",
        requested_by="private-requester",
        problem_type="tool:mysql.health" if number == 1 else "lock_wait",
        status=TaskStatus(status),
        created_at=created,
        started_at=None if status == "queued" else created,
        finished_at=None if status in {"queued", "running"} else created + timedelta(seconds=1),
    )


def test_history_auth_empty_and_openapi_contract():
    tools = FakeTools()
    client, _ = client_for(tools)
    with client:
        for headers in ({}, {"Authorization": "Bearer wrong"}):
            assert client.get("/api/v1/diagnoses", headers=headers).status_code == 401
        response = client.get("/api/v1/diagnoses", headers=HEADERS)
        assert response.json() == {"items": [], "next_cursor": None}
        assert response.headers["Cache-Control"] == "no-store"
        operation = client.get("/openapi.json", headers=HEADERS).json()["paths"][
            "/api/v1/diagnoses"
        ]["get"]
        assert operation["security"] == [{"HTTPBearer": []}]
        assert "requestBody" not in operation
        assert {p["name"] for p in operation["parameters"]} == {
            "instance_id",
            "status",
            "created_from",
            "created_until",
            "limit",
            "cursor",
        }
    assert not tools.calls


def test_keyset_pages_tie_breaker_and_new_task_insertion():
    tools = FakeTools()
    client, store = client_for(tools)
    for i in range(1, 6):
        task = history_task(i)
        store.tasks[task.task_id] = task
    with client:
        first = client.get("/api/v1/diagnoses?limit=2", headers=HEADERS).json()
        assert [i["task_id"] for i in first["items"]] == [str(UUID(int=5)), str(UUID(int=4))]
        assert set(first["items"][0]) == {
            "task_id",
            "instance_id",
            "database_type",
            "problem_type",
            "status",
            "created_at",
            "finished_at",
        }
        new = history_task(6, created=BASE + timedelta(seconds=1))
        store.tasks[new.task_id] = new
        second = client.get(
            "/api/v1/diagnoses",
            headers=HEADERS,
            params={"limit": 2, "cursor": first["next_cursor"]},
        ).json()
        assert [i["task_id"] for i in second["items"]] == [str(UUID(int=3)), str(UUID(int=2))]
        last = client.get(
            "/api/v1/diagnoses",
            headers=HEADERS,
            params={"limit": 2, "cursor": second["next_cursor"]},
        ).json()
        assert [i["task_id"] for i in last["items"]] == [str(UUID(int=1))]
        assert last["next_cursor"] is None
        assert last["items"][0]["problem_type"] == "tool:mysql.health"
        assert "private-requester" not in str(first)
        assert client.get("/api/v1/diagnoses?limit=1", headers=HEADERS).json()["items"][0][
            "task_id"
        ] == str(new.task_id)
    assert not tools.calls


@pytest.mark.parametrize("status", list(TaskStatus))
def test_history_status_and_disabled_instance_are_readable(status):
    client, store = client_for(FakeTools())
    for i, state in enumerate(TaskStatus, 1):
        task = history_task(i, status=state, instance="redis-lab-01")
        store.tasks[task.task_id] = task
    with client:
        page = client.get(
            "/api/v1/diagnoses",
            headers=HEADERS,
            params={"instance_id": "redis-lab-01", "status": status.value},
        ).json()
        assert len(page["items"]) == 1
        assert page["items"][0]["status"] == status.value
        assert page["items"][0]["instance_id"] == "redis-lab-01"


def test_creation_time_is_half_open_and_timezone_normalized():
    client, store = client_for(FakeTools())
    for i in range(1, 5):
        task = history_task(i, created=BASE + timedelta(hours=i - 1))
        store.tasks[task.task_id] = task
    params = {
        "created_from": "2026-01-01T08:00:00+08:00",
        "created_until": "2026-01-01T02:00:00Z",
        "limit": 1,
    }
    with client:
        page = client.get("/api/v1/diagnoses", headers=HEADERS, params=params).json()
        assert page["items"][0]["task_id"] == str(UUID(int=2))
        params["created_from"] = "2026-01-01T00:00:00Z"
        params["cursor"] = page["next_cursor"]
        final = client.get("/api/v1/diagnoses", headers=HEADERS, params=params)
        assert final.status_code == 200
        assert final.json()["items"][0]["task_id"] == str(UUID(int=1))
        assert final.json()["next_cursor"] is None


@pytest.mark.parametrize(
    "params",
    [
        {"limit": 0},
        {"limit": 101},
        {"limit": "1.2"},
        {"status": "healthy"},
        {"instance_id": ""},
        {"instance_id": "x" * 101},
        {"order_by": "id"},
        {"created_from": "2026-01-01T01:00:00"},
        {"created_until": "garbage"},
        {"created_from": "2026-01-02T00:00:00Z", "created_until": "2026-01-01T00:00:00Z"},
        {"created_from": "2026-01-01T00:00:00Z", "created_until": "2026-01-01T00:00:00Z"},
        {"cursor": ""},
        {"cursor": "%%%%"},
        {"cursor": "中文"},
        {"cursor": "e30"},
        {"cursor": "a" * 1025},
    ],
)
def test_invalid_history_queries_are_422_without_tools(params):
    tools = FakeTools()
    client, _ = client_for(tools)
    with client:
        response = client.get("/api/v1/diagnoses", headers=HEADERS, params=params)
        assert response.status_code == 422, response.text
    assert not tools.calls


def test_cursor_cannot_be_reused_with_changed_filters_and_unknown_instance_is_empty():
    client, store = client_for(FakeTools())
    for i in range(1, 4):
        task = history_task(i)
        store.tasks[task.task_id] = task
    with client:
        cursor = client.get("/api/v1/diagnoses?limit=1", headers=HEADERS).json()["next_cursor"]
        for filters in [
            {"status": "completed"},
            {"instance_id": "mysql-lab-01"},
            {"created_from": BASE.isoformat()},
        ]:
            assert (
                client.get(
                    "/api/v1/diagnoses", headers=HEADERS, params={"cursor": cursor, **filters}
                ).status_code
                == 422
            )
        for instance in ["unknown", "' OR 1=1 --"]:
            assert client.get(
                "/api/v1/diagnoses", headers=HEADERS, params={"instance_id": instance}
            ).json() == {"items": [], "next_cursor": None}
