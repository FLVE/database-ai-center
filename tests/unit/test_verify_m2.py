import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from dbic.core.models import ToolResult, utc_now
from tests.fakes import FakeTools
from tests.unit.test_api import TOKEN
from tests.unit.test_diagnoses import client_for


@pytest.fixture
def verifier(monkeypatch):
    monkeypatch.syspath_prepend(str(Path("scripts").resolve()))
    return importlib.import_module("verify_m2")


@pytest.mark.parametrize(
    "status,reason,accepted",
    [
        ("ok", "cumulative since reset", True),
        ("unavailable", "no timed application digest samples", True),
        ("unavailable", "digest consumers disabled or missing", True),
        ("unavailable", "statement instrumentation incomplete", True),
        ("unavailable", "mysql diagnostic source unavailable", False),
        ("unavailable", "mysql lab diagnostic credentials not configured", False),
        ("unavailable", "unknown reason", False),
        ("forbidden", "mysql diagnostic account permission denied", False),
        ("timeout", "mysql diagnostic query timed out or connection lost", False),
        ("error", "diagnostic tool failed", False),
    ],
)
def test_verifier_runs_three_api_scenarios(verifier, monkeypatch, capsys, status, reason, accepted):
    class Tools(FakeTools):
        async def run(self, *, tool_id, instance_id, params):
            if tool_id == "mysql.slow_query_summary":
                return ToolResult(
                    source=tool_id,
                    status=status,
                    observed_at=utc_now(),
                    duration_ms=1,
                    limitations=[reason],
                    payload={"digests": [{"digest": "abc", "calls": 2}]} if status == "ok" else {},
                )
            return await super().run(tool_id=tool_id, instance_id=instance_id, params=params)

    client, store = client_for(Tools())

    def request(path, token=None, body=None):
        response = client.request(
            "GET" if body is None else "POST",
            path,
            headers={"Authorization": f"Bearer {token}"} if token else {},
            json=body,
        )
        return response.status_code, response.json()

    monkeypatch.setattr(verifier, "request", request)
    monkeypatch.setattr(verifier.subprocess, "run", lambda *a, **kw: SimpleNamespace(stdout=TOKEN))
    monkeypatch.setattr(verifier.sys, "argv", ["verify_m2.py", "--skip-suite"])
    with client:
        if accepted:
            verifier.main()
        else:
            with pytest.raises(
                RuntimeError, match=f"mysql.slow_query_summary status={status}"
            ) as exc:
                verifier.main()
            assert reason in str(exc.value)
    assert len(store.tasks) == 3
    output = capsys.readouterr().out
    assert ("PASS: M2 API verification" in output) == accepted
    assert ("verified explicit degradation" in output) == (accepted and status == "unavailable")


def evidence(source, status="ok", limits=None):
    return {"source": source, "status": status, "limitations": limits or [], "evidence_id": source}


@pytest.mark.parametrize("fault", ["completed", "missing_explanation", "unsupported_finding"])
def test_digest_gap_requires_honest_report(verifier, fault):
    source = "mysql.slow_query_summary"
    reason = "no timed application digest samples"
    current = [evidence("mysql.health"), evidence(source, "unavailable", [reason])]
    detail = {
        "task": {"status": "partial"},
        "report": {
            "missing_evidence": [f"{source}: unavailable", f"{source}: {reason}"],
            "findings": [],
        },
    }
    if fault == "completed":
        detail["task"]["status"] = "completed"
    elif fault == "missing_explanation":
        detail["report"]["missing_evidence"] = []
    else:
        detail["report"]["findings"] = [{"evidence_ids": [source]}]
    with pytest.raises(RuntimeError):
        verifier.verify_realtime("slow_query", current, detail)


@pytest.mark.parametrize(
    "problem,current",
    [
        ("slow_query", [evidence("mysql.health", "empty"), evidence("mysql.slow_query_summary")]),
        ("slow_query", [evidence("mysql.health"), evidence("mysql.health")]),
        (
            "lock_wait",
            [
                evidence("mysql.health"),
                evidence(
                    "mysql.lock_chain", "unavailable", ["no timed application digest samples"]
                ),
            ],
        ),
        (
            "slow_query",
            [evidence("mysql.health"), evidence("mysql.slow_query_summary", "unavailable")],
        ),
        (
            "slow_query",
            [
                evidence("mysql.health"),
                evidence(
                    "mysql.slow_query_summary",
                    "unavailable",
                    ["no timed application digest samples", "unknown failure"],
                ),
            ],
        ),
    ],
)
def test_other_failures_are_not_accepted(verifier, problem, current):
    with pytest.raises(RuntimeError):
        verifier.verify_realtime(problem, current, {})


@pytest.mark.parametrize("search_status", ["ok", "empty", "unavailable", "forbidden"])
def test_search_only_verification_requires_events(verifier, monkeypatch, capsys, search_status):
    from dbic.core.history import EVENT_SOURCE, request_window

    class Search:
        async def search_events(self, *, instance_id, start, end, categories):
            return ToolResult(
                source=EVENT_SOURCE,
                kind="log",
                window=request_window(instance_id, start, end),
                status=search_status,
                observed_at=utc_now(),
                duration_ms=1,
                payload={"events": [{"code": "LAB_EVENT"}]} if search_status == "ok" else {},
            )

    client, _ = client_for(FakeTools(), search=Search())

    def request(path, token=None, body=None):
        response = client.request(
            "GET" if body is None else "POST",
            path,
            headers={"Authorization": f"Bearer {token}"} if token else {},
            json=body,
        )
        result = response.json()
        if response.status_code == 200 and path == "/api/v1/capabilities":
            result["historical_backends"] = {"metrics": "null", "search": "elastic"}
        return response.status_code, result

    monkeypatch.setattr(verifier, "request", request)
    monkeypatch.setattr(verifier.subprocess, "run", lambda *a, **kw: SimpleNamespace(stdout=TOKEN))
    monkeypatch.setattr(verifier.sys, "argv", ["verify_m2.py", "--require-search", "--skip-suite"])
    with client:
        if search_status == "ok":
            verifier.main()
            assert "with Elasticsearch event evidence" in capsys.readouterr().out
        else:
            with pytest.raises(RuntimeError, match="historical evidence missing"):
                verifier.main()
