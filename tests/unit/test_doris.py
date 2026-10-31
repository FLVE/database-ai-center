import asyncio
from datetime import timedelta
from unittest.mock import MagicMock

import httpx
import pytest
from pydantic import SecretStr

from dbic.adapters.http import BackendFailure
from dbic.core.doris_rules import doris_findings
from dbic.core.evidence import normalize
from dbic.core.models import DiagnosticTask, ToolResult, utc_now
from dbic.plugins.doris.diagnostics import DorisDiagnostics, duration_ms, profile_summary

COLUMNS = [
    "Profile ID",
    "Task Type",
    "Start Time",
    "End Time",
    "Total",
    "Task State",
    "User",
    "Default Catalog",
    "Default Db",
    "Sql Statement",
]


def raw_profiles(count=1):
    return {
        "column_names": COLUMNS,
        "rows": [
            {"User": "diag", "Total": "1s2ms", "Task State": "OK", "Sql Statement": "private"}
            for _ in range(count)
        ],
    }


def test_profile_redaction_and_limits():
    result = profile_summary(raw_profiles(21))
    assert len(result.samples) == 20 and result.truncated
    assert result.samples[0].duration_ms == 1002 and "private" not in result.model_dump_json()
    assert not profile_summary(raw_profiles(0)).samples
    other = raw_profiles()
    other["rows"][0]["User"] = "root"
    assert not profile_summary(other).samples
    retained = raw_profiles()
    retained["rows"][0]["Total"] = "3sec342ms"
    assert profile_summary(retained).samples[0].duration_ms == 3342


@pytest.mark.parametrize("value", ["NaN", "-1ms", "1foo", 12, None, "1ms extra"])
def test_malformed_duration(value):
    with pytest.raises(ValueError):
        duration_ms(value)


@pytest.mark.parametrize(
    "data",
    [
        {"column_names": [], "rows": []},
        {"column_names": COLUMNS, "rows": [True]},
        {"column_names": COLUMNS, "rows": "private"},
    ],
)
def test_malformed_profile(data):
    with pytest.raises(ValueError):
        profile_summary(data)


def run(monkeypatch, response, tool="doris.fe_be_health", **overrides):
    calls = []
    monkeypatch.setattr(DorisDiagnostics, "_version", lambda self: None)

    def handler(request):
        calls.append(request)
        if isinstance(response, Exception):
            raise response
        return response

    adapter = DorisDiagnostics(SecretStr("private"), transport=httpx.MockTransport(handler))
    result = asyncio.run(
        adapter.run(
            **{
                "tool_id": tool,
                "instance_id": "doris-lab-01",
                "params": {},
            }
            | overrides
        )
    )
    return result, calls


@pytest.mark.parametrize(
    "response,status",
    [
        (
            httpx.Response(
                200, json={"code": 0, "data": {"online_backend_num": 1, "total_backend_num": 1}}
            ),
            "ok",
        ),
        (httpx.Response(200, json={"code": 401, "data": "private"}), "forbidden"),
        (httpx.Response(401), "forbidden"),
        (httpx.Response(403), "forbidden"),
        (httpx.Response(503), "unavailable"),
        (httpx.Response(302), "unavailable"),
        (httpx.Response(200, text="x" * 262145), "error"),
        (httpx.Response(200, text="private"), "error"),
        (
            httpx.Response(
                200, json={"code": 0, "data": {"online_backend_num": 2, "total_backend_num": 1}}
            ),
            "error",
        ),
        (httpx.ReadTimeout("private"), "timeout"),
        (httpx.ConnectError("private"), "unavailable"),
    ],
)
def test_http_failure_semantics(monkeypatch, response, status):
    result, calls = run(monkeypatch, response)
    assert result.status == status and "private" not in result.model_dump_json()
    assert len(calls) == 1 and calls[0].method == "GET"
    assert str(calls[0].url) == "http://doris-fe:8030/api/health"


def test_profile_path_and_no_arbitrary_commands(monkeypatch):
    response = httpx.Response(200, json={"code": 0, "data": raw_profiles()})
    result, calls = run(monkeypatch, response, tool="doris.query_profile_summary")
    assert result.status == "ok" and "private" not in str(result.payload)
    assert str(calls[0].url) == "http://doris-fe:8030/rest/v1/query_profile/"
    result, calls = run(monkeypatch, response, params={"sql": "DROP DATABASE business"})
    assert result.status == "forbidden" and not calls


def test_evidence_ownership_freshness_and_schema():
    task = DiagnosticTask(
        instance_id="doris-lab-01",
        database_type="doris",
        problem_type="cluster_health",
        requested_by="test",
    )
    item = normalize(
        task,
        ToolResult(
            source="doris.fe_be_health",
            status="ok",
            observed_at=utc_now(),
            duration_ms=1,
            payload={"online_backends": 1, "total_backends": 1},
        ),
    )
    findings = doris_findings(task, [item])
    assert len(findings) == 1 and findings[0].evidence_ids == [item.evidence_id]
    for change in (
        {"redacted": False},
        {"quality": "inferred"},
        {"instance_id": "other"},
        {"status": "empty"},
        {"payload": {"online_backends": True}},
    ):
        assert not doris_findings(task, [item.model_copy(update=change)])
    assert not doris_findings(task, [item], now=item.observed_at + timedelta(seconds=91))


@pytest.mark.parametrize(
    "version,accepted",
    [
        ("Doris version doris-3.0.8-rc01-build", True),
        ("Doris version doris-3.0.9-build", False),
        ("MySQL 8.4", False),
        (None, False),
    ],
)
def test_fixed_version_probe(monkeypatch, version, accepted):
    cursor = MagicMock()
    cursor.fetchone.return_value = (version,)
    cursor.__enter__.return_value = cursor
    conn = MagicMock()
    conn.__enter__.return_value = conn
    conn.cursor.return_value = cursor
    connect = MagicMock(return_value=conn)
    monkeypatch.setattr("dbic.plugins.doris.diagnostics.pymysql.connect", connect)
    adapter = DorisDiagnostics(SecretStr("fake"))
    if accepted:
        adapter._version()
    else:
        with pytest.raises(BackendFailure, match="unsupported Doris version"):
            adapter._version()
    cursor.execute.assert_called_once_with("SELECT @@version_comment")
    assert connect.call_args.kwargs["host"] == "doris-fe"
    assert connect.call_args.kwargs["user"] == "diag"
