import httpx
import pytest

from dbic.adapters.history_factory import historical_adapters
from dbic.settings import Settings
from tests.fakes import FakeTools
from tests.unit.test_api import TOKEN
from tests.unit.test_diagnoses import HEADERS, client_for
from tests.unit.test_history_adapters import WINDOW, matrix, search_response


@pytest.mark.parametrize(
    "metrics_enabled,search_enabled", [(False, False), (True, False), (False, True), (True, True)]
)
@pytest.mark.parametrize("failure", [None, 401, 403, 503, "timeout", "disconnect"])
def test_optional_backend_switches_preserve_realtime_findings(
    metrics_enabled, search_enabled, failure
):
    calls = []

    def handler(request):
        calls.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("secret-upstream", request=request)
        if failure == "disconnect":
            raise httpx.ConnectError("secret-upstream", request=request)
        if failure:
            return httpx.Response(failure, text="secret-upstream")
        return httpx.Response(200, json=matrix() if request.method == "GET" else search_response())

    config = Settings(
        api_token=TOKEN,
        pg_password="test-only",
        metrics_backend="prometheus" if metrics_enabled else "null",
        search_backend="elastic" if search_enabled else "null",
        prometheus_url="http://localhost:9090",
        elastic_url="http://localhost:9200",
        prometheus_instance_value="mysql-lab:9104",
    )
    metrics, search = historical_adapters(config, transport=httpx.MockTransport(handler))
    tools = FakeTools("ok", {"edges": [{"waiter_session": 2, "blocker_session": 1}]})
    client, _ = client_for(tools, metrics=metrics, search=search)
    with client:
        response = client.post(
            "/api/v1/diagnoses",
            headers=HEADERS,
            json={"instance_id": "mysql-lab-01", "problem_type": "lock_wait", "window": WINDOW},
        )
        assert response.status_code == 202
        path = "/api/v1/diagnoses/" + response.json()["task_id"]
        detail = client.get(path, headers=HEADERS).json()
        evidence = client.get(path + "/evidence", headers=HEADERS).json()
        assert detail["task"]["status"] == (
            "completed" if metrics_enabled and search_enabled and not failure else "partial"
        )
        assert len(detail["steps"]) == len(evidence) == 4
        realtime = [e for e in evidence if e["kind"] == "database_state"]
        history = [e for e in evidence if e["kind"] != "database_state"]
        assert len(realtime) == len(history) == 2
        assert all(e["window"] is None for e in realtime)
        assert all(e["window"] == detail["task"]["window"] for e in history)
        realtime_ids = {e["evidence_id"] for e in realtime}
        findings = detail["report"]["findings"]
        timeline = detail["report"]["timeline"]
        assert timeline == sorted(timeline, key=lambda entry: entry.split(" ")[0])
        assert any(set(f["evidence_ids"]) <= realtime_ids for f in findings)
        ids = {e["evidence_id"] for e in evidence}
        assert all(f["evidence_ids"] and set(f["evidence_ids"]) <= ids for f in findings)
        assert not any(f["certainty"] == "confirmed_root_cause" for f in findings)
        assert "secret-upstream" not in str(detail) + str(evidence)
        assert len(calls) == int(metrics_enabled) + int(search_enabled)
        calls.clear()
        response = client.post(
            "/api/v1/diagnoses",
            headers=HEADERS,
            json={"instance_id": "mysql-lab-01", "problem_type": "lock_wait"},
        )
        assert response.status_code == 202 and calls == []


@pytest.mark.parametrize(
    "window",
    [
        {"start": "2020-01-01T00:00:00Z", "end": "2020-01-03T00:00:00Z"},
        {"start": "2999-01-01T00:00:00Z", "end": "2999-01-01T00:30:00Z"},
        {"start": "2020-01-01", "end": "2020-01-02"},
    ],
)
def test_window_validation_precedes_task_creation(window):
    tools = FakeTools()
    client, store = client_for(tools)
    with client:
        response = client.post(
            "/api/v1/diagnoses",
            headers=HEADERS,
            json={"instance_id": "mysql-lab-01", "problem_type": "lock_wait", "window": window},
        )
        assert response.status_code == 422
    assert not tools.calls and not store.tasks


def test_port_exception_preserves_mysql_evidence():
    class Broken:
        async def query_range(self, **kwargs):
            raise RuntimeError("private-token")

        async def search_events(self, **kwargs):
            raise TimeoutError("private-token")

    client, _ = client_for(FakeTools(), metrics=Broken(), search=Broken())
    with client:
        response = client.post(
            "/api/v1/diagnoses",
            headers=HEADERS,
            json={"instance_id": "mysql-lab-01", "problem_type": "lock_wait", "window": WINDOW},
        )
        path = "/api/v1/diagnoses/" + response.json()["task_id"]
        detail = client.get(path, headers=HEADERS).json()
        assert detail["task"]["status"] == "partial"
        assert [step["status"] for step in detail["steps"]][-2:] == ["error", "timeout"]
        assert detail["report"]["findings"] and "private-token" not in str(detail)


def test_equivalent_timezone_normalizes_to_utc():
    from tests.unit.test_history_adapters import adapters, query

    metrics, _ = adapters(lambda _: httpx.Response(200, json=matrix()))
    result = query(
        metrics, "metric", start="2020-01-01T08:00:00+08:00", end="2020-01-01T08:00:02+08:00"
    )
    assert result.status == "ok" and not result.limitations
    assert result.window.start.isoformat() == "2020-01-01T00:00:00+00:00"
